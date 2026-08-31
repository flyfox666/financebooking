from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Invoice(Base):
    __tablename__ = "invoice"
    __table_args__ = (UniqueConstraint("book_id", "kind", "invoice_no", name="uq_invoice_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    invoice_type: Mapped[str] = mapped_column(String(16), default="general")
    invoice_code: Mapped[str | None] = mapped_column(String(32), default=None)
    invoice_no: Mapped[str] = mapped_column(String(32), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    seller_name: Mapped[str] = mapped_column(String(128), default="")
    seller_tax_no: Mapped[str] = mapped_column(String(32), default="")
    buyer_name: Mapped[str] = mapped_column(String(128), default="")
    buyer_tax_no: Mapped[str] = mapped_column(String(32), default="")
    goods_name: Mapped[str] = mapped_column(String(200), default="")
    amount_excl: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    amount_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    status: Mapped[str] = mapped_column(String(16), default="normal")
    voucher_id: Mapped[int | None] = mapped_column(ForeignKey("voucher.id"), default=None)


class TaxParam(Base):
    __tablename__ = "tax_param"
    __table_args__ = (UniqueConstraint("book_id", "tax", "name", "effective_from", name="uq_tax_param"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    tax: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    effective_from: Mapped[date] = mapped_column(Date, default=date(2026, 1, 1))
    effective_to: Mapped[date | None] = mapped_column(Date, default=None)
