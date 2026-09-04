"""账务勾稽关系校验（试算平衡之外的纵深审计）。

对账套某期间执行四类关系校验：
1. 方向/余额异常：科目期末余额方向与科目性质相反（资产贷方、负债借方、货币资金赤字等），
   试算平衡抓不到这一类"平衡但错误"的账。
2. 未分配利润↔净利润：资产负债表未分配利润本年变动 与 本年累计净利润 勾稽
   （无利润分配/盈余公积/以前年度损益调整时二者应相等）。
3. 货币资金↔现金流：货币资金期末净额 与 现金流量表期末现金余额 对账。
4. 跨期衔接：本期期初 与 上期期末 的连续一致性（启用首期跳过）。
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.ledger.balances import (
    ZERO,
    fmt_amount,
    next_period,
    opening_nets,
    trial_balance,
)
from app.ledger.report_service import balance_sheet, income_statement

TOLERANCE = Decimal("0.01")
CASH_CODES = ("1001", "1002", "1012")


def _prev_period(period: str) -> str:
    """返回上一个账期，如 2026-09 → 2026-08。"""
    year, month = int(period[:4]), int(period[5:7])
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def _pick(rows: list[dict], key: str, field: str) -> Decimal:
    for row in rows:
        if row.get("key") == key:
            raw = row.get(field, "0.00")
            return Decimal(str(raw).replace(",", ""))
    return ZERO


# ---------- 1. 方向/余额异常 ----------

def direction_anomalies(db: Session, *, book_id: int, period: str) -> list[dict]:
    """按科目性质校验期末余额方向，返回异常清单（借正净额与科目方向相反）。"""
    issues: list[dict] = []
    for row in trial_balance(db, book_id=book_id, period=period)["rows"]:
        net = Decimal(row["closing_debit"]) - Decimal(row["closing_credit"])
        if net == 0:
            continue
        direction = int(row["direction"])  # 1=借，-1=贷
        # 借方科目出现贷方余额 / 贷方科目出现借方余额
        if (direction == 1 and net < 0) or (direction == -1 and net > 0):
            code = row["account_code"]
            cash_negative = code[:4] in CASH_CODES and net < 0
            issues.append(
                {
                    "code": code,
                    "name": row["account_name"],
                    "balance": fmt_amount(abs(net)),
                    "balance_side": "贷" if net < 0 else "借",
                    "expected_side": "借" if direction == 1 else "贷",
                    "issue": "货币资金赤字" if cash_negative else ("贷方余额" if direction == 1 else "借方余额"),
                    "severity": "high" if cash_negative else "warn",
                    "is_active": row["is_active"],
                }
            )
    return issues


# ---------- 2. 未分配利润 ↔ 净利润 ----------

def profit_reconciliation(db: Session, *, book_id: int, period: str) -> dict:
    """BS 未分配利润本年变动 与 IS 本年累计净利润 勾稽。

    表结法下 BS「未分配利润」= 3103 + 3104 + 全部损益科目，其本年变动
    应等于本年累计净利润；差异通常来自利润分配/盈余公积/以前年度损益调整。
    """
    bs = balance_sheet(db, book_id=book_id, period=period)
    inc = income_statement(db, book_id=book_id, period=period)
    closing = _pick(bs["rows"], "undistributed_profits", "closing")
    year_begin = _pick(bs["rows"], "undistributed_profits", "year_begin")
    ytd_np = Decimal(str(inc["ytd_net_profit"]).replace(",", ""))
    delta = closing - year_begin
    diff = delta - ytd_np
    ok = abs(diff) <= TOLERANCE
    return {
        "ok": ok,
        "closing": fmt_amount(closing),
        "year_begin": fmt_amount(year_begin),
        "change": fmt_amount(delta),
        "ytd_net_profit": fmt_amount(ytd_np),
        "diff": fmt_amount(diff),
        "note": "" if ok else "差异可能来自当期利润分配、盈余公积计提或以前年度损益调整",
    }


# ---------- 3. 货币资金 ↔ 现金流 ----------

def cash_reconciliation(db: Session, *, book_id: int, period: str) -> dict:
    """货币资金期末净额 与 现金流量表期末现金余额 对账（应相等）。"""
    from app.ledger.cashflow import cash_flow

    closing = sum(
        (opening_nets(db, book_id, before_period=next_period(period)).get(c, ZERO) for c in CASH_CODES),
        ZERO,
    )
    cf = cash_flow(db, book_id=book_id, period=period)
    cf_closing = ZERO
    for row in cf["rows"]:
        if row.get("name") == "期末现金及现金等价物余额":
            cf_closing = Decimal(str(row["month"]).replace(",", ""))
            break
    diff = closing - cf_closing
    return {
        "ok": abs(diff) <= TOLERANCE,
        "monetary_closing": fmt_amount(closing),
        "cashflow_closing": fmt_amount(cf_closing),
        "diff": fmt_amount(diff),
        "note": "" if abs(diff) <= TOLERANCE else "现金流量表期末余额与货币资金科目余额不一致，检查现流分类是否漏科目",
    }


# ---------- 4. 跨期衔接 ----------

def period_continuity(db: Session, *, book_id: int, period: str) -> dict:
    """本期期初 与 上期期末 的连续性。启用首期（无上年同期）跳过。"""
    from app.models.book import Book

    book = db.get(Book, book_id)
    prev = _prev_period(period)
    if book and book.start_period and prev < book.start_period:
        return {"ok": True, "issues": [], "note": "启用首期，无上期可比"}

    current_opening = opening_nets(db, book_id, before_period=period)
    prev_opening = opening_nets(db, book_id, before_period=prev)
    from app.ledger.balances import gross_sums

    prev_sums = gross_sums(db, book_id, prev, prev)
    prev_closing: dict[str, Decimal] = {}
    for code, net in prev_opening.items():
        prev_closing[code] = net
    for code, sums in prev_sums.items():
        prev_closing[code] = prev_closing.get(code, ZERO) + sums[0] - sums[1]

    issues = []
    codes = set(current_opening) | set(prev_closing)
    for code in sorted(codes):
        cur = current_opening.get(code, ZERO)
        pre = prev_closing.get(code, ZERO)
        if abs(cur - pre) > TOLERANCE:
            issues.append({"code": code, "opening": fmt_amount(cur), "prev_closing": fmt_amount(pre)})
    return {
        "ok": not issues,
        "issues": issues,
        "note": "" if not issues else "本期期初与上期期末不一致（可能期初被改或结转异常）",
    }


# ---------- 聚合 ----------

def run_checks(db: Session, *, book_id: int, period: str) -> dict:
    """一次返回四类勾稽校验结果。"""
    direction = direction_anomalies(db, book_id=book_id, period=period)
    profit = profit_reconciliation(db, book_id=book_id, period=period)
    cash = cash_reconciliation(db, book_id=book_id, period=period)
    continuity = period_continuity(db, book_id=book_id, period=period)
    return {
        "book_id": book_id,
        "period": period,
        "direction": {"ok": not direction, "issues": direction},
        "profit": profit,
        "cash": cash,
        "continuity": continuity,
        "all_ok": (not direction) and profit["ok"] and cash["ok"] and continuity["ok"],
    }