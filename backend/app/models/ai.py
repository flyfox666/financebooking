from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIDoc(Base):
    __tablename__ = "ai_doc"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(16), default="invoice")
    source_kind: Mapped[str] = mapped_column(String(16), default="")
    file_name: Mapped[str] = mapped_column(String(200), default="")
    staging_path: Mapped[str] = mapped_column(String(300), default="")
    fields_json: Mapped[str] = mapped_column(Text, default="{}")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    layers_json: Mapped[str] = mapped_column(Text, default="[]")
    model_output: Mapped[str] = mapped_column(Text, default="")
    voucher_id: Mapped[int | None] = mapped_column(ForeignKey("voucher.id"), default=None)
    status: Mapped[str] = mapped_column(String(16), default="parsed", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AiStyleSetting(Base):
    """账套级 AI 记账偏好：行业标签（多选）+ 业务描述（限长）。一账套一条。"""

    __tablename__ = "ai_style_setting"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("book.id"), unique=True, index=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    business_desc: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
