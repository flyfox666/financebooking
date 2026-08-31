from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.ledger.balances import fmt_amount


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    invoice_type: str
    invoice_code: str | None
    invoice_no: str
    invoice_date: date
    seller_name: str
    seller_tax_no: str
    buyer_name: str
    buyer_tax_no: str
    goods_name: str
    amount_excl: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    amount_total: Decimal
    status: str

    @field_serializer("amount_excl", "tax_amount", "amount_total")
    def ser_amount(self, value: Decimal, _info) -> str:
        return fmt_amount(value)

    @field_serializer("tax_rate")
    def ser_rate(self, value: Decimal, _info) -> str:
        return str(value)


class ImportResultOut(BaseModel):
    created: int
    updated: int
    skipped: int
    kind: str


class StampContract(BaseModel):
    type: Literal["purchase", "tech", "lease"]
    amount: Decimal = Decimal("0")


class StampCalcIn(BaseModel):
    year: int
    contracts: list[StampContract] = Field(default_factory=list)


class IitCalcIn(BaseModel):
    month: int = Field(ge=1, le=12)
    cumulative_income: Decimal = Decimal("0")
    cumulative_deductions: Decimal = Decimal("0")
    withheld_prev: Decimal = Decimal("0")


class TaxParamUpsertIn(BaseModel):
    tax: Literal["vat", "surtax", "cit", "iit", "stamp"]
    name: str
    value: Decimal
    effective_from: date = date(2026, 1, 1)
    effective_to: date | None = None
