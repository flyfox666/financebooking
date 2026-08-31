from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.ledger.balances import fmt_amount, gross_sums
from app.ledger.tax.params import get_param

TWO = Decimal("0.01")
ZERO = Decimal("0.00")

CONTRACT_TYPES = {"purchase": "purchase_rate", "tech": "tech_rate", "lease": "lease_rate"}


def calc_stamp(
    db: Session,
    *,
    book_id: int,
    year: int,
    contracts: list[dict] | None = None,
) -> dict:
    discount = get_param(db, book_id, "stamp", "discount")
    books_rate = get_param(db, book_id, "stamp", "books_rate")

    sums = gross_sums(db, book_id, f"{year}-01", f"{year}-12")
    increase = ZERO
    for code in ("3001", "3002"):
        debit, credit = sums.get(code, [ZERO, ZERO])
        increase += credit - debit
    if increase < 0:
        increase = ZERO
    books_tax = (increase * books_rate * discount).quantize(TWO, rounding=ROUND_HALF_UP)

    contract_rows = []
    contracts_tax = ZERO
    for item in contracts or []:
        ctype = str(item.get("type") or "").strip()
        amount = Decimal(str(item.get("amount") or 0))
        if ctype not in CONTRACT_TYPES:
            raise ValueError(f"未知合同类型：{ctype}")
        rate = get_param(db, book_id, "stamp", CONTRACT_TYPES[ctype])
        tax = (amount * rate * discount).quantize(TWO, rounding=ROUND_HALF_UP)
        contracts_tax += tax
        contract_rows.append(
            {
                "type": ctype,
                "amount": fmt_amount(amount),
                "tax": fmt_amount(tax),
            }
        )

    total = books_tax + contracts_tax
    return {
        "year": year,
        "books_increase": fmt_amount(increase),
        "books_tax": fmt_amount(books_tax),
        "contracts": contract_rows,
        "contracts_tax": fmt_amount(contracts_tax),
        "total": fmt_amount(total),
        "note": "营业账簿按实收资本+资本公积本年增加额计税；认缴未实缴不征；仅就增加部分缴纳",
    }
