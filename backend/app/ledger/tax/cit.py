from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.ledger.balances import fmt_amount
from app.ledger.report_service import income_statement
from app.ledger.tax.params import get_param

TWO = Decimal("0.01")
ZERO = Decimal("0.00")


def _quarter_end(year: int, quarter: int) -> str:
    return f"{year}-{quarter * 3:02d}"


def calc_cit(
    db: Session,
    *,
    book_id: int,
    year: int,
    quarter: int,
    employees: int | None = None,
    assets: Decimal | None = None,
    prepaid_prev: Decimal = ZERO,
) -> dict:
    period = _quarter_end(year, quarter)
    statement = income_statement(db, book_id=book_id, period=period)
    profit_ytd = Decimal(statement["ytd_net_profit"].replace(",", ""))

    max_income = get_param(db, book_id, "cit", "max_taxable_income")
    max_employees = int(get_param(db, book_id, "cit", "max_employees"))
    max_assets = get_param(db, book_id, "cit", "max_assets")
    included_rate = get_param(db, book_id, "cit", "small_included_rate")
    rate = get_param(db, book_id, "cit", "rate")

    conditions = {
        "profit_within_limit": ZERO <= profit_ytd <= max_income,
        "employees_within_limit": employees is None or employees <= max_employees,
        "assets_within_limit": assets is None or assets <= max_assets,
    }
    preferential = all(conditions.values())

    taxable_base = profit_ytd if profit_ytd > 0 else ZERO
    if preferential:
        tax_total = (taxable_base * included_rate * rate).quantize(TWO, rounding=ROUND_HALF_UP)
    else:
        tax_total = (taxable_base * Decimal("0.25")).quantize(TWO, rounding=ROUND_HALF_UP)

    prepaid_prev = Decimal(str(prepaid_prev))
    prepaid = tax_total - prepaid_prev
    if prepaid < 0:
        prepaid = ZERO

    hints = []
    if profit_ytd < 0:
        hints.append("本年累计亏损，本期无需预缴企业所得税")
    if employees is None or assets is None:
        hints.append("请手工确认季度平均从业人数与资产总额（季初+季末÷2，全年四季平均）")
    hints.append("汇算清缴时按纳税调整后所得计算，本结果为预缴辅助数")

    return {
        "year": year,
        "quarter": quarter,
        "period": f"{year}Q{quarter}",
        "profit_ytd": fmt_amount(profit_ytd),
        "taxable_base": fmt_amount(taxable_base),
        "preferential": preferential,
        "actual_rate": "5%" if preferential else "25%",
        "tax_total_ytd": fmt_amount(tax_total),
        "prepaid_prev": fmt_amount(prepaid_prev),
        "prepaid_this": fmt_amount(prepaid),
        "conditions": conditions,
        "hints": hints,
    }
