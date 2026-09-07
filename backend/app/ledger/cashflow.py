"""现金流量表（会小企 03 表 · 直接法简化版）。

按「现金类科目（1001/1002/1012）变动的对方科目」自动分类到经营/投资/筹资活动；
现金及现金等价物内部划转（如提现）自动剔除；期末余额与科目余额自动对账。
分类映射为代码表，出现未知对方科目时归入「收到/支付其他与经营活动有关的现金」。
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.balances import fmt_amount, opening_nets
from app.models.voucher import Voucher, VoucherLine

CASH_ACCOUNTS = ("1001", "1002", "1012")
POSTED_STATUSES = ("posted", "voided")
ZERO = Decimal("0.00")

INFLOW_MAP = {
    "5001": "sales", "5051": "sales", "1122": "sales", "2203": "sales",
    "5111": "invest_return", "1606": "asset_dispose",
    "3001": "capital_in", "3002": "capital_in",
    "2001": "borrow_in", "2501": "borrow_in", "2701": "borrow_in",
    "5301": "other_in", "2241": "other_in", "1221": "other_in",
}

OUTFLOW_MAP = {
    "5401": "purchase", "5402": "purchase", "2202": "purchase", "1123": "purchase",
    "1401": "purchase", "1402": "purchase", "1403": "purchase", "1405": "purchase",
    "1411": "purchase", "2201": "purchase",
    "2211": "staff", "2221": "taxes", "5403": "taxes",
    "1601": "capex", "1604": "capex", "1605": "capex", "1701": "capex", "1801": "capex",
    "1101": "invest_out", "1501": "invest_out", "1511": "invest_out",
    "2001": "borrow_repay", "2501": "borrow_repay", "2701": "borrow_repay",
    "2232": "dividend", "3104": "dividend",
    "5601": "other_out", "5602": "other_out", "5603": "other_out", "5711": "other_out",
    "1221": "other_out", "4301": "other_out", "2241": "other_out",
}

ITEM_LABELS = {
    "sales": "销售商品、提供劳务收到的现金",
    "invest_return": "取得投资收益收到的现金",
    "asset_dispose": "处置固定资产、无形资产和其他长期资产收回的现金净额",
    "capital_in": "吸收投资收到的现金",
    "borrow_in": "取得借款收到的现金",
    "other_in": "收到其他与经营活动有关的现金",
    "purchase": "购买商品、接受劳务支付的现金",
    "staff": "支付给职工以及为职工支付的现金",
    "taxes": "支付的各项税费",
    "capex": "购建固定资产、无形资产和其他长期资产支付的现金",
    "invest_out": "投资支付的现金",
    "borrow_repay": "偿还债务支付的现金",
    "dividend": "分配股利、利润或偿付利息支付的现金",
    "other_out": "支付其他与经营活动有关的现金",
}


def _classify(amounts: dict) -> dict:
    buckets: dict[str, Decimal] = {}
    for code, amount in amounts.items():
        mapping = INFLOW_MAP if amount > 0 else OUTFLOW_MAP
        item = mapping.get(code, "other_in" if amount > 0 else "other_out")
        buckets[item] = buckets.get(item, ZERO) + amount
    return buckets


def _flows(db: Session, book_id: int, period_from: str, period_to: str) -> dict:
    rows = db.execute(
        select(Voucher, VoucherLine)
        .join(VoucherLine, VoucherLine.voucher_id == Voucher.id)
        .where(
            Voucher.book_id == book_id,
            Voucher.period >= period_from,
            Voucher.period <= period_to,
            Voucher.status.in_(POSTED_STATUSES),
        )
        .order_by(Voucher.voucher_date, Voucher.voucher_no, VoucherLine.line_no)
    ).all()

    grouped: dict[int, list[tuple[str, Decimal, str | None]]] = {}
    for voucher, line in rows:
        grouped.setdefault(voucher.id, []).append(
            (
                line.account_code,
                Decimal(str(line.debit)) - Decimal(str(line.credit)),
                line.cf_item if line.cf_item in ITEM_LABELS else None,
            )
        )

    buckets: dict[str, Decimal] = {}
    for lines in grouped.values():
        cash_lines = [(c, a, cf) for c, a, cf in lines if c[:4] in CASH_ACCOUNTS]
        cash_net = sum((a for _, a, _ in cash_lines), ZERO)
        if cash_net == 0 and not any(c[:4] not in CASH_ACCOUNTS for c, _, _ in lines):
            continue
        # 优先采用行级现金流量标注（支持一张凭证拆进多个流量项目）
        tagged_total = ZERO
        for _, amount, cf in cash_lines:
            if cf and amount != 0:
                buckets[cf] = buckets.get(cf, ZERO) + amount
                tagged_total += amount
        # 未标注的现金净额退回「对方最大行科目」推断
        remainder = cash_net - tagged_total
        if remainder != 0:
            others = [(c, a) for c, a, _ in lines if c[:4] not in CASH_ACCOUNTS]
            if not others:
                continue
            main_code, _ = max(others, key=lambda item: abs(item[1]))
            mapping = INFLOW_MAP if remainder > 0 else OUTFLOW_MAP
            item = mapping.get(main_code, mapping.get(main_code[:4], "other_in" if remainder > 0 else "other_out"))
            buckets[item] = buckets.get(item, ZERO) + remainder
    return buckets


def _section(name: str, inflow_items: list[str], outflow_items: list[str], flows: dict) -> list[dict]:
    rows: list[dict] = [{"name": name, "bold": True}]
    inflow_total = ZERO
    outflow_total = ZERO
    for item in inflow_items:
        value = flows.get(item, ZERO)
        inflow_total += value
        rows.append({"name": ITEM_LABELS[item], "value": fmt_amount(value), "bold": False})
    rows.append({"name": "经营活动现金流入小计" if "经营" in name else f"{name}现金流入小计", "value": fmt_amount(inflow_total), "bold": True})
    for item in outflow_items:
        value = -flows.get(item, ZERO)
        outflow_total += value
        rows.append({"name": ITEM_LABELS[item], "value": fmt_amount(value), "bold": False})
    rows.append({"name": "经营活动现金流出小计" if "经营" in name else f"{name}现金流出小计", "value": fmt_amount(outflow_total), "bold": True})
    net = inflow_total - outflow_total
    rows.append({"name": f"{name}产生的现金流量净额", "value": fmt_amount(net), "bold": True})
    rows.append({"__net__": True, "name": name, "value": net})
    return rows


def cash_flow(db: Session, *, book_id: int, period: str) -> dict:
    from app.models.book import Book

    book = db.get(Book, book_id)
    year = period[:4]
    results = {}
    for label, (start, end) in {
        "month": (period, period),
        "year_to_date": (f"{year}-01", period),
    }.items():
        flows = _flows(db, book_id, start, end)
        operating = _section("一、经营活动", ["sales", "other_in"], ["purchase", "staff", "taxes", "other_out"], flows)
        investing = _section("二、投资活动", ["asset_dispose", "invest_return"], ["capex", "invest_out"], flows)
        financing = _section("三、筹资活动", ["capital_in", "borrow_in"], ["borrow_repay", "dividend"], flows)
        nets = [row["value"] for section in (operating, investing, financing) for row in section if row.get("__net__")]
        net_increase = sum(nets, ZERO)
        opening = sum(
            (
                opening_nets(db, book_id, before_period=start).get(code, ZERO)
                for code in CASH_ACCOUNTS
            ),
            ZERO,
        )
        results[label] = {
            "rows": operating + investing + financing,
            "net_increase": net_increase,
            "opening": opening,
            "closing": opening + net_increase,
        }

    def flat(rows):
        output = []
        for row in rows:
            if row.get("__net__"):
                continue
            output.append(
                {
                    "name": row["name"],
                    "value": fmt_amount(row["value"]) if "value" in row else "",
                    "bold": row.get("bold", False),
                }
            )
        return output

    month_rows = flat(results["month"]["rows"])
    ytd_rows = flat(results["year_to_date"]["rows"])
    month_rows.append({"name": "四、现金及现金等价物净增加额", "value": fmt_amount(results["month"]["net_increase"]), "bold": True})
    month_rows.append({"name": "加：期初现金及现金等价物余额", "value": fmt_amount(results["month"]["opening"]), "bold": True})
    month_rows.append({"name": "期末现金及现金等价物余额", "value": fmt_amount(results["month"]["closing"]), "bold": True})
    ytd_rows.append({"name": "四、现金及现金等价物净增加额", "value": fmt_amount(results["year_to_date"]["net_increase"]), "bold": True})
    ytd_rows.append({"name": "加：期初现金及现金等价物余额", "value": fmt_amount(results["year_to_date"]["opening"]), "bold": True})
    ytd_rows.append({"name": "期末现金及现金等价物余额", "value": fmt_amount(results["year_to_date"]["closing"]), "bold": True})

    return {
        "book_id": book_id,
        "book_name": book.name if book else "",
        "period": period,
        "rows": [
            {"name": row["name"], "month": row["value"], "year_to_date": ytd["value"], "bold": row["bold"]}
            for row, ytd in zip(month_rows, ytd_rows)
        ],
    }
