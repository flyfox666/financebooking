from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OpeningBalance(Base):
    __tablename__ = "opening_balance"
    __table_args__ = (UniqueConstraint("book_id", "account_code", name="uq_opening_book_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    account_code: Mapped[str] = mapped_column(String(32))
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)


class PeriodClose(Base):
    __tablename__ = "period_close"
    __table_args__ = (UniqueConstraint("book_id", "period", name="uq_period_close"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))
    closed_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    closed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PeriodBalance(Base):
    __tablename__ = "ledger_period_balance"
    __table_args__ = (
        UniqueConstraint("book_id", "account_code", "period", name="uq_period_balance"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    account_code: Mapped[str] = mapped_column(String(32))
    period: Mapped[str] = mapped_column(String(7), index=True)
    opening_debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    opening_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    period_debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    period_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    closing_debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    closing_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)


class ReportTemplate(Base):
    __tablename__ = "report_template"
    __table_args__ = (UniqueConstraint("book_id", "report", "key", name="uq_report_template_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    report: Mapped[str] = mapped_column(String(8))
    key: Mapped[str] = mapped_column(String(64))
    row_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))
    side: Mapped[str | None] = mapped_column(String(8), default=None)
    formula: Mapped[str | None] = mapped_column(Text, default=None)
    in_total: Mapped[bool] = mapped_column(Boolean, default=True)
