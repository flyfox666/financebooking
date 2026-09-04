from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
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


class UserBook(Base):
    """用户↔账套成员关系（多租户权限地基）。

    role 取 book 级角色：bookkeeper（制单）/ auditor（审核）/ admin（账套管理）。
    全局 admin 用户天然可见所有账套（不依赖本表）。
    """

    __tablename__ = "user_book"
    __table_args__ = (UniqueConstraint("user_id", "book_id", name="uq_user_book"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="bookkeeper")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
