from datetime import date

from pydantic import BaseModel, Field


class SuggestIn(BaseModel):
    book_id: int
    doc_id: int | None = None
    note: str = ""


class ConfirmIn(BaseModel):
    doc_id: int
    voucher_date: date
    lines: list[dict] = Field(min_length=2)
