import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.balances import (
    ZERO,
    fmt_amount,
    gross_sums,
    leaf_accounts,
    next_period,
    opening_nets,
    _with_parent_rollups_gross,
    _with_parent_rollups_nets,
)
from app.models.book import Book
from app.models.report import ReportTemplate


def _load_template(db: Session, book_id: int, report: str) -> list[dict]:
    rows = db.scalars(
        select(ReportTemplate)
        .where(ReportTemplate.book_id == book_id, ReportTemplate.report == report)
        .order_by(ReportTemplate.row_no)
    ).all()
    if not rows:
        from app.ledger.exceptions import BookError

        raise BookError("报表模板未初始化")
    return [
        {
            "key": row.key,
            "row_no": row.row_no,
            "name": row.name,
            "kind": row.kind,
            "side": row.side,
            "formula": json.loads(row.formula) if row.formula else [],
            "in_total": row.in_total,
        }
        for row in rows
    ]


def balance_sheet(db: Session, *, book_id: int, period: str) -> dict:
    book = db.get(Book, book_id)
    year = period[:4]
    template = _load_template(db, book_id, "bs")

    closing_nets = _with_parent_rollups_nets(
        db, book_id, opening_nets(db, book_id, before_period=next_period(period))
    )
    year_begin_nets = _with_parent_rollups_nets(
        db, book_id, opening_nets(db, book_id, before_period=f"{year}-01")
    )

    def evaluate(nets: dict[str, Decimal]) -> dict[str, Decimal]:
        values: dict[str, Decimal] = {}
        for row in template:
            if row["kind"] == "line":
                total = ZERO
                for code, sign in row["formula"]:
                    net = nets.get(code, ZERO)
                    signed = net if row["side"] == "debit" else -net
                    total += Decimal(sign) * signed
                values[row["key"]] = total
            else:
                total = ZERO
                for term in row["formula"]:
                    value = values[term[1:]]
                    total += value if term[0] == "+" else -value
                values[row["key"]] = total
        return values

    closing_values = evaluate(closing_nets)
    year_begin_values = evaluate(year_begin_nets)

    rows = [
        {
            "key": row["key"],
            "row_no": row["row_no"],
            "name": row["name"],
            "closing": fmt_amount(closing_values[row["key"]]),
            "year_begin": fmt_amount(year_begin_values[row["key"]]),
            "bold": row["kind"] == "calc",
        }
        for row in template
    ]
    return {
        "book_id": book_id,
        "book_name": book.name if book else "",
        "period": period,
        "rows": rows,
        "total_assets": fmt_amount(closing_values["total_assets"]),
        "total_liabilities_and_equity": fmt_amount(closing_values["total_liabilities_and_equity"]),
        "year_begin_total_assets": fmt_amount(year_begin_values["total_assets"]),
        "year_begin_total_liabilities_and_equity": fmt_amount(
            year_begin_values["total_liabilities_and_equity"]
        ),
    }


def income_statement(db: Session, *, book_id: int, period: str) -> dict:
    book = db.get(Book, book_id)
    year = period[:4]
    template = _load_template(db, book_id, "is")

    month_sums = _with_parent_rollups_gross(
        db, book_id, gross_sums(db, book_id, period, period, exclude_pnl_carryover=True)
    )
    ytd_sums = _with_parent_rollups_gross(
        db,
        book_id,
        gross_sums(db, book_id, f"{year}-01", period, exclude_pnl_carryover=True),
    )

    def evaluate(sums: dict[str, list[Decimal]]) -> dict[str, Decimal]:
        values: dict[str, Decimal] = {}
        for row in template:
            if row["kind"] == "line":
                total = ZERO
                for code, sign in row["formula"]:
                    debit, credit = sums.get(code, [ZERO, ZERO])
                    directional = (credit - debit) if row["side"] == "credit" else (debit - credit)
                    total += Decimal(sign) * directional
                values[row["key"]] = total
            else:
                total = ZERO
                for term in row["formula"]:
                    value = values[term[1:]]
                    total += value if term[0] == "+" else -value
                values[row["key"]] = total
        return values

    month_values = evaluate(month_sums)
    ytd_values = evaluate(ytd_sums)

    rows = [
        {
            "key": row["key"],
            "row_no": row["row_no"],
            "name": row["name"],
            "month": fmt_amount(month_values[row["key"]]),
            "year_to_date": fmt_amount(ytd_values[row["key"]]),
            "bold": row["kind"] == "calc",
        }
        for row in template
    ]
    return {
        "book_id": book_id,
        "book_name": book.name if book else "",
        "period": period,
        "year": year,
        "rows": rows,
        "month_net_profit": fmt_amount(month_values["net_profit"]),
        "ytd_net_profit": fmt_amount(ytd_values["net_profit"]),
    }
