from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Contact(Base):
    __tablename__ = "contact"
    __table_args__ = (UniqueConstraint("book_id", "name", name="uq_contact_book_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(128))
    tax_no: Mapped[str] = mapped_column(String(32), default="")
    ctype: Mapped[str] = mapped_column(String(16), default="customer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
