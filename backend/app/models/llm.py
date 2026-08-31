from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LLMProvider(Base):
    __tablename__ = "llm_provider"
    __table_args__ = (UniqueConstraint("name", name="uq_llm_provider_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(16), default="openai")
    base_url: Mapped[str] = mapped_column(String(200))
    api_key: Mapped[str] = mapped_column(String(400))
    model: Mapped[str] = mapped_column(String(64))
    vision_model: Mapped[str | None] = mapped_column(String(64), default=None)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
