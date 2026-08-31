from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def _ser_amount(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


class VoucherLineIn(BaseModel):
    summary: str = Field(min_length=1, max_length=200)
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")


class VoucherCreateIn(BaseModel):
    voucher_date: date
    attachment_count: int = Field(default=1, ge=0)
    source: Literal["manual", "ai"] = "manual"
    lines: list[VoucherLineIn] = Field(min_length=2)


class VoucherUpdateIn(BaseModel):
    voucher_date: date | None = None
    attachment_count: int | None = Field(default=None, ge=0)
    lines: list[VoucherLineIn] | None = None


class VoucherLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_no: int
    summary: str
    account_code: str
    debit: Decimal
    credit: Decimal
    contact_id: int | None = None

    @field_serializer("debit", "credit")
    def ser_amount(self, value: Decimal, _info) -> str:
        return _ser_amount(value)


class VoucherOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    word: str
    voucher_no: int
    voucher_no_display: str
    period: str
    voucher_date: date
    attachment_count: int
    status: str
    source: str
    carryover_type: str | None
    reverses_voucher_id: int | None
    voided_by_voucher_id: int | None
    total_debit: Decimal
    total_credit: Decimal
    created_by: int
    audited_by: int | None
    posted_by: int | None
    created_at: datetime
    lines: list[VoucherLineOut]

    @field_serializer("total_debit", "total_credit")
    def ser_total(self, value: Decimal, _info) -> str:
        return _ser_amount(value)
