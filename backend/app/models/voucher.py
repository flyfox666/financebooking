from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Voucher(Base):
    __tablename__ = "voucher"
    __table_args__ = (UniqueConstraint("book_id", "period", "voucher_no", name="uq_voucher_no"),
                      UniqueConstraint("reverses_voucher_id", name="uq_reversal_original"))

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    word: Mapped[str] = mapped_column(String(4), default="记")
    voucher_no: Mapped[int] = mapped_column(Integer)
    period: Mapped[str] = mapped_column(String(7), index=True)
    voucher_date: Mapped[date] = mapped_column(Date)
    attachment_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    total_debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    total_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    carryover_type: Mapped[str | None] = mapped_column(String(16), default=None)
    reverses_voucher_id: Mapped[int | None] = mapped_column(ForeignKey("voucher.id"), default=None)
    voided_by_voucher_id: Mapped[int | None] = mapped_column(ForeignKey("voucher.id"), default=None)
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    edited_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), default=None)
    editor_ids_json: Mapped[str] = mapped_column(Text, default='[]')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    audited_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), default=None)
    audited_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), default=None)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    lines: Mapped[list["VoucherLine"]] = relationship(
        back_populates="voucher",
        cascade="all, delete-orphan",
        order_by="VoucherLine.line_no",
    )

    @property
    def voucher_no_display(self) -> str:
        return f"{self.word}字第{self.voucher_no:04d}号"


class VoucherLine(Base):
    __tablename__ = "voucher_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("voucher.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(200))
    account_code: Mapped[str] = mapped_column(String(32))
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contact.id"), default=None)
    cf_item: Mapped[str | None] = mapped_column(String(32), default=None)

    voucher: Mapped["Voucher"] = relationship(back_populates="lines")
