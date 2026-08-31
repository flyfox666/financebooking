from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tax import TaxParam

DEFAULT_EFFECTIVE = (date(2026, 1, 1), date(2027, 12, 31))

DEFAULT_TAX_PARAMS: dict[tuple[str, str], str] = {
    ("vat", "rate"): "0.01",
    ("vat", "threshold_quarter"): "300000",
    ("vat", "threshold_month"): "100000",
    ("surtax", "urban_rate"): "0.07",
    ("surtax", "edu_rate"): "0.03",
    ("surtax", "local_edu_rate"): "0.02",
    ("surtax", "discount"): "0.5",
    ("cit", "small_included_rate"): "0.25",
    ("cit", "rate"): "0.20",
    ("cit", "max_taxable_income"): "3000000",
    ("cit", "max_employees"): "300",
    ("cit", "max_assets"): "50000000",
    ("stamp", "books_rate"): "0.00025",
    ("stamp", "purchase_rate"): "0.0003",
    ("stamp", "tech_rate"): "0.0003",
    ("stamp", "lease_rate"): "0.001",
    ("stamp", "discount"): "0.5",
}


def seed_tax_params(db: Session, book_id: int) -> None:
    existing = set(
        db.scalars(select(TaxParam.tax, TaxParam.name).where(TaxParam.book_id == book_id))
    )
    for (tax, name), value in DEFAULT_TAX_PARAMS.items():
        if (tax, name) in existing:
            continue
        db.add(
            TaxParam(
                book_id=book_id,
                tax=tax,
                name=name,
                value=Decimal(value),
                effective_from=DEFAULT_EFFECTIVE[0],
                effective_to=DEFAULT_EFFECTIVE[1],
            )
        )
    db.commit()


def get_param(db: Session, book_id: int, tax: str, name: str, on_date: date | None = None) -> Decimal:
    on_date = on_date or date.today()
    row = db.scalar(
        select(TaxParam)
        .where(
            TaxParam.book_id == book_id,
            TaxParam.tax == tax,
            TaxParam.name == name,
            TaxParam.effective_from <= on_date,
            (TaxParam.effective_to.is_(None)) | (TaxParam.effective_to >= on_date),
        )
        .order_by(TaxParam.effective_from.desc())
    )
    if row is not None:
        return Decimal(str(row.value))
    if (tax, name) in DEFAULT_TAX_PARAMS:
        return Decimal(DEFAULT_TAX_PARAMS[(tax, name)])
    raise KeyError(f"未定义的税务参数：{tax}/{name}")
