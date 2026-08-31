from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.ledger.exceptions import VoucherError
from app.models.account import Account
from app.models.report import OpeningBalance
from app.models.voucher import Voucher, VoucherLine

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")
POSTED_STATUSES = ("posted", "voided")


def fmt_amount(value: Decimal) -> str:
    return format(Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP), "f")


def next_period(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    if month == 12:
        return f"{year + 1}-01"
    return f"{year}-{month + 1:02d}"


def _split(net: Decimal) -> tuple[Decimal, Decimal]:
    if net > 0:
        return net, ZERO
    if net < 0:
        return ZERO, -net
    return ZERO, ZERO


def opening_nets(db: Session, book_id: int, before_period: str | None) -> dict[str, Decimal]:
    nets: dict[str, Decimal] = {}
    for row in db.scalars(select(OpeningBalance).where(OpeningBalance.book_id == book_id)):
        nets[row.account_code] = nets.get(row.account_code, ZERO) + row.debit - row.credit
    if before_period:
        rows = db.execute(
            select(VoucherLine.account_code, VoucherLine.debit, VoucherLine.credit)
            .join(Voucher, VoucherLine.voucher_id == Voucher.id)
            .where(
                Voucher.book_id == book_id,
                Voucher.period < before_period,
                Voucher.status.in_(POSTED_STATUSES),
            )
        ).all()
        for code, debit, credit in rows:
            nets[code] = nets.get(code, ZERO) + Decimal(str(debit)) - Decimal(str(credit))
    return nets


def gross_sums(
    db: Session,
    book_id: int,
    period_from: str,
    period_to: str | None = None,
    *,
    exclude_pnl_carryover: bool = False,
) -> dict[str, list[Decimal]]:
    stmt = (
        select(VoucherLine.account_code, VoucherLine.debit, VoucherLine.credit)
        .join(Voucher, VoucherLine.voucher_id == Voucher.id)
        .where(
            Voucher.book_id == book_id,
            Voucher.period >= period_from,
            Voucher.status.in_(POSTED_STATUSES),
        )
    )
    if period_to:
        stmt = stmt.where(Voucher.period <= period_to)
    if exclude_pnl_carryover:
        stmt = stmt.where(
            ~and_(Voucher.source == "carryover", Voucher.carryover_type == "pnl")
        )
    sums: dict[str, list[Decimal]] = {}
    for code, debit, credit in db.execute(stmt).all():
        entry = sums.setdefault(code, [ZERO, ZERO])
        entry[0] += Decimal(str(debit))
        entry[1] += Decimal(str(credit))
    return sums


def leaf_accounts(db: Session, book_id: int) -> list[Account]:
    return list(
        db.scalars(
            select(Account)
            .where(Account.book_id == book_id, Account.is_leaf.is_(True))
            .order_by(Account.code)
        )
    )


def _with_parent_rollups_nets(
    db: Session, book_id: int, nets: dict[str, Decimal]
) -> dict[str, Decimal]:
    merged = dict(nets)
    for leaf in leaf_accounts(db, book_id):
        prefix = leaf.code[:4]
        if prefix != leaf.code:
            merged[prefix] = merged.get(prefix, ZERO) + nets.get(leaf.code, ZERO)
    return merged


def _with_parent_rollups_gross(
    db: Session, book_id: int, sums: dict[str, list[Decimal]]
) -> dict[str, list[Decimal]]:
    merged = {code: list(entry) for code, entry in sums.items()}
    for leaf in leaf_accounts(db, book_id):
        prefix = leaf.code[:4]
        if prefix != leaf.code:
            entry = merged.setdefault(prefix, [ZERO, ZERO])
            g = sums.get(leaf.code, [ZERO, ZERO])
            entry[0] += g[0]
            entry[1] += g[1]
    return merged


def _interleave(parent_rows: list[dict], leaf_rows: list[dict]) -> list[dict]:
    children: dict[str, list[dict]] = {}
    for row in leaf_rows:
        prefix = row["account_code"][:4]
        children.setdefault(prefix, []).append(row)
    result = []
    used = set()
    for parent in parent_rows:
        result.append(parent)
        for child in children.get(parent["account_code"], []):
            result.append(child)
            used.add(child["account_code"])
    for row in leaf_rows:
        if row["account_code"] not in used:
            result.append(row)
    return result


def trial_balance(db: Session, *, book_id: int, period: str, complete: bool = False) -> dict:
    from app.models.book import Book

    book = db.get(Book, book_id)
    opening = opening_nets(db, book_id, before_period=period)
    sums = gross_sums(db, book_id, period, period)

    opening_rollup = {code: ZERO for code in sums}
    for code, net in opening.items():
        opening_rollup[code] = opening_rollup.get(code, ZERO) + net

    leaves = leaf_accounts(db, book_id)

    def leaf_row(account):
        o_net = opening.get(account.code, ZERO)
        o_dr, o_cr = _split(o_net)
        p_dr, p_cr = sums.get(account.code, [ZERO, ZERO])
        c_net = o_net + p_dr - p_cr
        c_dr, c_cr = _split(c_net)
        return {
            "account_code": account.code,
            "account_name": account.name,
            "direction": account.direction,
            "is_active": account.is_active,
            "level": account.level,
            "parent_code": account.parent_code,
            "has_aux": bool(account.aux_types),
            "opening_debit": fmt_amount(o_dr),
            "opening_credit": fmt_amount(o_cr),
            "period_debit": fmt_amount(p_dr),
            "period_credit": fmt_amount(p_cr),
            "closing_debit": fmt_amount(c_dr),
            "closing_credit": fmt_amount(c_cr),
        }

    rows = [leaf_row(account) for account in leaves]

    if complete:
        leaf_opening: dict[str, Decimal] = {}
        for account in leaves:
            leaf_opening[account.code] = opening.get(account.code, ZERO)
        merged_sums = _with_parent_rollups_gross(db, book_id, sums)
        merged_opening: dict[str, Decimal] = dict(leaf_opening)
        for leaf in leaves:
            prefix = leaf.code[:4]
            if prefix != prefix or len(prefix) == 4:
                merged_opening[prefix] = merged_opening.get(prefix, ZERO) + leaf_opening.get(leaf.code, ZERO)
        parents = db.scalars(
            select(Account)
            .where(Account.book_id == book_id, Account.level == 1)
            .order_by(Account.code)
        ).all()
        parent_rows = []
        for account in parents:
            o_net = merged_opening.get(account.code, ZERO)
            o_dr, o_cr = _split(o_net)
            p_dr, p_cr = merged_sums.get(account.code, [ZERO, ZERO])
            c_net = o_net + p_dr - p_cr
            c_dr, c_cr = _split(c_net)
            parent_rows.append(
                {
                    "account_code": account.code,
                    "account_name": account.name,
                    "direction": account.direction,
                    "is_active": account.is_active,
                    "level": 1,
                    "parent_code": None,
                    "has_aux": bool(account.aux_types),
                    "is_rollup": True,
                    "opening_debit": fmt_amount(o_dr),
                    "opening_credit": fmt_amount(o_cr),
                    "period_debit": fmt_amount(p_dr),
                    "period_credit": fmt_amount(p_cr),
                    "closing_debit": fmt_amount(c_dr),
                    "closing_credit": fmt_amount(c_cr),
                }
            )
        rows = _interleave(parent_rows, rows)

    totals = {
        "opening_debit": ZERO, "opening_credit": ZERO,
        "period_debit": ZERO, "period_credit": ZERO,
        "closing_debit": ZERO, "closing_credit": ZERO,
    }
    for row in rows:
        if row.get("is_rollup"):
            continue
        totals["opening_debit"] += Decimal(row["opening_debit"])
        totals["opening_credit"] += Decimal(row["opening_credit"])
        totals["period_debit"] += Decimal(row["period_debit"])
        totals["period_credit"] += Decimal(row["period_credit"])
        totals["closing_debit"] += Decimal(row["closing_debit"])
        totals["closing_credit"] += Decimal(row["closing_credit"])
    formatted_totals = {key: fmt_amount(value) for key, value in totals.items()}
    is_balanced = (
        totals["opening_debit"] == totals["opening_credit"]
        and totals["period_debit"] == totals["period_credit"]
        and totals["closing_debit"] == totals["closing_credit"]
    )
    return {
        "book_id": book_id,
        "book_name": book.name if book else "",
        "period": period,
        "rows": rows,
        "totals": formatted_totals,
        "is_balanced": is_balanced,
    }


def general_ledger(db: Session, *, book_id: int, period: str) -> dict:
    from app.models.book import Book

    book = db.get(Book, book_id)
    opening = opening_nets(db, book_id, before_period=period)
    sums = gross_sums(db, book_id, period, period)
    parents = {
        a.code: a
        for a in db.scalars(
            select(Account)
            .where(Account.book_id == book_id, Account.level == 1)
            .order_by(Account.code)
        )
    }
    rollup = {code: {"o": ZERO, "dr": ZERO, "cr": ZERO} for code in parents}
    for leaf in leaf_accounts(db, book_id):
        prefix = leaf.code[:4]
        entry = rollup.setdefault(prefix, {"o": ZERO, "dr": ZERO, "cr": ZERO})
        entry["o"] += opening.get(leaf.code, ZERO)
        g = sums.get(leaf.code, [ZERO, ZERO])
        entry["dr"] += g[0]
        entry["cr"] += g[1]
    rows = []
    for code in sorted(rollup):
        entry = rollup[code]
        o_dr, o_cr = _split(entry["o"])
        c_net = entry["o"] + entry["dr"] - entry["cr"]
        c_dr, c_cr = _split(c_net)
        account = parents.get(code)
        rows.append(
            {
                "account_code": code,
                "account_name": account.name if account else code,
                "opening_debit": fmt_amount(o_dr),
                "opening_credit": fmt_amount(o_cr),
                "period_debit": fmt_amount(entry["dr"]),
                "period_credit": fmt_amount(entry["cr"]),
                "closing_debit": fmt_amount(c_dr),
                "closing_credit": fmt_amount(c_cr),
            }
        )
    return {
        "book_id": book_id,
        "book_name": book.name if book else "",
        "period": period,
        "rows": rows,
    }


def _direction_label(directional_balance: Decimal, direction: int) -> str:
    if directional_balance > 0:
        return "借" if direction == 1 else "贷"
    if directional_balance < 0:
        return "贷" if direction == 1 else "借"
    return "平"


def detail_ledger(
    db: Session, *, book_id: int, account_code: str, period_from: str, period_to: str
) -> dict:
    from app.models.book import Book

    book = db.get(Book, book_id)
    account = db.scalar(
        select(Account).where(Account.book_id == book_id, Account.code == account_code)
    )
    if account is None:
        raise VoucherError(f"科目 {account_code} 不存在")
    if not account.is_leaf:
        raise VoucherError("明细账查询需要选择末级科目")

    opening_net = opening_nets(db, book_id, before_period=period_from).get(account_code, ZERO)
    direction = account.direction
    balance = opening_net * direction

    rows = [
        {
            "row_type": "opening",
            "voucher_date": "",
            "voucher_no_display": "",
            "summary": "期初余额",
            "debit": fmt_amount(ZERO),
            "credit": fmt_amount(ZERO),
            "balance": fmt_amount(abs(balance)),
            "balance_direction": _direction_label(balance, direction),
        }
    ]

    stmt = (
        select(Voucher, VoucherLine)
        .join(VoucherLine, VoucherLine.voucher_id == Voucher.id)
        .where(
            Voucher.book_id == book_id,
            VoucherLine.account_code == account_code,
            Voucher.period >= period_from,
            Voucher.period <= period_to,
            Voucher.status.in_(POSTED_STATUSES),
        )
        .order_by(Voucher.voucher_date, Voucher.voucher_no, VoucherLine.line_no)
    )
    for voucher, line in db.execute(stmt).all():
        balance += (Decimal(str(line.debit)) - Decimal(str(line.credit))) * direction
        rows.append(
            {
                "row_type": "line",
                "voucher_date": voucher.voucher_date.isoformat(),
                "voucher_no_display": voucher.voucher_no_display,
                "summary": line.summary,
                "debit": fmt_amount(Decimal(str(line.debit))),
                "credit": fmt_amount(Decimal(str(line.credit))),
                "balance": fmt_amount(abs(balance)),
                "balance_direction": _direction_label(balance, direction),
            }
        )

    return {
        "book_id": book_id,
        "book_name": book.name if book else "",
        "account_code": account.code,
        "account_name": account.name,
        "direction": direction,
        "period_from": period_from,
        "period_to": period_to,
        "rows": rows,
        "closing_balance": fmt_amount(abs(balance)),
        "closing_direction": _direction_label(balance, direction),
    }
