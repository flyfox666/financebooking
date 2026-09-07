from datetime import date

from pydantic import BaseModel, Field


class SuggestIn(BaseModel):
    book_id: int
    doc_id: int | None = None
    note: str = ""


class ChatIn(BaseModel):
    book_id: int
    doc_id: int | None = None
    history: list[dict] = Field(default_factory=list)


class ConfirmIn(BaseModel):
    doc_id: int
    voucher_date: date
    lines: list[dict] = Field(min_length=2)
    invoice_kind: str | None = None
    risk_fingerprint: str | None = None
    risk_reason: str = Field(default="", max_length=500)
