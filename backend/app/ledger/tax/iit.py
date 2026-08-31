from decimal import ROUND_HALF_UP, Decimal

from app.ledger.balances import fmt_amount

TWO = Decimal("0.01")
ZERO = Decimal("0.00")

BRACKETS: list[tuple[Decimal, Decimal, Decimal]] = [
    (Decimal("36000"), Decimal("0.03"), Decimal("0")),
    (Decimal("144000"), Decimal("0.10"), Decimal("2520")),
    (Decimal("300000"), Decimal("0.20"), Decimal("16920")),
    (Decimal("420000"), Decimal("0.25"), Decimal("31920")),
    (Decimal("660000"), Decimal("0.30"), Decimal("52920")),
    (Decimal("960000"), Decimal("0.35"), Decimal("85920")),
    (None, Decimal("0.45"), Decimal("181920")),
]


def _annual_tax(taxable: Decimal) -> Decimal:
    if taxable <= 0:
        return ZERO
    for upper, rate, deduction in BRACKETS:
        if upper is None or taxable <= upper:
            return (taxable * rate - deduction).quantize(TWO, rounding=ROUND_HALF_UP)
    return ZERO


def calc_iit(
    *,
    month: int,
    cumulative_income: Decimal,
    cumulative_deductions: Decimal,
    withheld_prev: Decimal = ZERO,
) -> dict:
    income = Decimal(str(cumulative_income))
    deductions = Decimal(str(cumulative_deductions))
    withheld_prev = Decimal(str(withheld_prev))
    taxable = income - deductions
    if taxable < 0:
        taxable = ZERO
    tax_total = _annual_tax(taxable)
    current = tax_total - withheld_prev
    if current < 0:
        current = ZERO
    return {
        "month": month,
        "cumulative_income": fmt_amount(income),
        "cumulative_deductions": fmt_amount(deductions),
        "cumulative_taxable": fmt_amount(taxable),
        "tax_total_ytd": fmt_amount(tax_total),
        "withheld_prev": fmt_amount(withheld_prev),
        "withhold_this_month": fmt_amount(current),
        "note": "累计预扣法：应扣税额 = 累计应纳税所得额×预扣率-速算扣除数-已预扣税额；次月15日前申报",
    }
