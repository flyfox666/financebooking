from decimal import Decimal

from pydantic import BaseModel, Field


class OpeningItem(BaseModel):
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")


class OpeningSetIn(BaseModel):
    items: list[OpeningItem] = Field(min_length=1)
