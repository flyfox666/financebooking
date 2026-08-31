import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger.balances import fmt_amount
from app.ledger.tax.params import get_param
from app.models.tax import Invoice

TWO = Decimal("0.01")
ZERO = Decimal("0.00")


def quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    last_month = quarter * 3
    first_month = last_month - 2
    start = date(year, first_month, 1)
    end = date(year, last_month, calendar.monthrange(year, last_month)[1])
    return start, end


def _invoice_excl_tax(inv: Invoice) -> tuple[Decimal, Decimal]:
    rate = Decimal(str(inv.tax_rate))
    total = Decimal(str(inv.amount_total))
    if rate <= 0:
        return total, ZERO
    excl = (total / (Decimal(1) + rate)).quantize(TWO, rounding=ROUND_HALF_UP)
    return excl, total - excl


def _surtax_components(vat: Decimal, db: Session, book_id: int, on_date: date) -> dict:
    components = {}
    if vat <= 0:
        for key in ("urban", "edu", "local_edu", "total"):
            components[key] = ZERO
        return components
    discount = get_param(db, book_id, "surtax", "discount", on_date)
    urban = (vat * get_param(db, book_id, "surtax", "urban_rate", on_date) * discount).quantize(TWO, rounding=ROUND_HALF_UP)
    edu = (vat * get_param(db, book_id, "surtax", "edu_rate", on_date) * discount).quantize(TWO, rounding=ROUND_HALF_UP)
    local_edu = (vat * get_param(db, book_id, "surtax", "local_edu_rate", on_date) * discount).quantize(TWO, rounding=ROUND_HALF_UP)
    components = {"urban": urban, "edu": edu, "local_edu": local_edu, "total": urban + edu + local_edu}
    return components


def calc_vat(
    db: Session,
    *,
    book_id: int,
    year: int,
    quarter: int,
    unissued_income: Decimal = ZERO,
) -> dict:
    on_date = date(year, min(quarter * 3, 12), 1)
    rate = get_param(db, book_id, "vat", "rate", on_date)
    threshold = get_param(db, book_id, "vat", "threshold_quarter", on_date)
    start, end = quarter_bounds(year, quarter)

    invoices = db.scalars(
        select(Invoice).where(
            Invoice.book_id == book_id,
            Invoice.kind == "sales",
            Invoice.status.in_(["normal", "red"]),
            Invoice.invoice_date >= start,
            Invoice.invoice_date <= end,
        )
    ).all()

    special_total = general_total = ZERO
    special_excl = general_excl = ZERO
    special_tax = general_tax = ZERO
    for inv in invoices:
        excl, tax_part = _invoice_excl_tax(inv)
        if inv.invoice_type == "special":
            special_total += Decimal(str(inv.amount_total))
            special_excl += excl
            special_tax += tax_part
        else:
            general_total += Decimal(str(inv.amount_total))
            general_excl += excl
            general_tax += tax_part

    unissued = Decimal(str(unissued_income))
    unissued_excl = (unissued / (Decimal(1) + rate)).quantize(TWO, rounding=ROUND_HALF_UP) if unissued else ZERO
    unissued_tax = unissued - unissued_excl

    quarter_total = special_total + general_total + unissued
    exempt = quarter_total <= threshold

    if exempt:
        vat_payable = special_tax if special_tax > 0 else ZERO
        exempt_sales_excl = general_excl + unissued_excl
        exempt_vat = general_tax + unissued_tax
        taxable_sales_excl = special_excl
    else:
        vat_payable = special_tax + general_tax + unissued_tax
        if vat_payable < 0:
            vat_payable = ZERO
        exempt_sales_excl = ZERO
        exempt_vat = ZERO
        taxable_sales_excl = special_excl + general_excl + unissued_excl

    surtax = _surtax_components(vat_payable, db, book_id, on_date)

    hints = ["销售表现以《增值税及附加税费申报表（小规模纳税人适用）》为准"]
    if exempt:
        hints.append(
            "普票及未开票销售额（免税部分）填报第 10 栏「小微企业免税销售额」等免税栏次；专票销售额填第 1 栏"
        )
    else:
        hints.append("季度销售额超过起征点，全部销售额（含普票与未开票）按征收率计税，填第 1 栏")

    return {
        "year": year,
        "quarter": quarter,
        "period": f"{year}Q{quarter}",
        "rate": fmt_amount(rate * 100).rstrip(".00") + "%",
        "threshold": fmt_amount(threshold),
        "quarter_total_incl": fmt_amount(quarter_total),
        "special_total_incl": fmt_amount(special_total),
        "general_total_incl": fmt_amount(general_total),
        "unissued_income": fmt_amount(unissued),
        "exempt": exempt,
        "taxable_sales_excl": fmt_amount(taxable_sales_excl),
        "vat_payable": fmt_amount(vat_payable),
        "exempt_sales_excl": fmt_amount(exempt_sales_excl),
        "exempt_vat": fmt_amount(exempt_vat),
        "surtax": {k: fmt_amount(v) for k, v in surtax.items()},
        "hints": hints,
    }
