from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Book(Base):
    __tablename__ = "book"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    tax_no: Mapped[str] = mapped_column(String(32), default="")
    taxpayer_type: Mapped[str] = mapped_column(String(16), default="small_scale")
    entity_type: Mapped[str] = mapped_column(String(16), default="company")
    accounting_standard: Mapped[str] = mapped_column(String(16), default="sme_2011")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    start_period: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
