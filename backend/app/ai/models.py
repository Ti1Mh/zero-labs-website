"""Database models for Brand Voice, Personas, and AI settings."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BrandPersona(Base):
    """A team's unique brand voice and stylistic persona for AI generation."""

    __tablename__ = "brand_personas"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone_traits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    forbidden_words: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    signature_phrases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    sample_posts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    target_audience: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
