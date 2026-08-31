from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class BookCreate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    tax_no: str = ""
    taxpayer_type: Literal["small_scale", "general"] = "small_scale"
    entity_type: Literal["company", "individual", "partnership"] = "company"
    start_period: str


class BookOut(BaseModel):
    id: int
    name: str
    tax_no: str
    taxpayer_type: str
    entity_type: str
    accounting_standard: str
    currency: str
    start_period: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
