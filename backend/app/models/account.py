from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Account(Base):
    __tablename__ = "account"
    __table_args__ = (UniqueConstraint("book_id", "code", name="uq_account_book_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(16))
    direction: Mapped[int] = mapped_column(Integer)
    parent_code: Mapped[str | None] = mapped_column(String(32), default=None, index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=True)
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False)
    aux_types: Mapped[str] = mapped_column(String(64), default="")
