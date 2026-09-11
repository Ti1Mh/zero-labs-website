"""Database model for immutable append-only AI usage ledger."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIUsageLedger(Base):
    """Immutable record tracking every AI model invocation, tokens, and financial cost."""

    __tablename__ = "ai_usage_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_jobs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    tokens_prompt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_completion: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. generate, optimize, repurpose
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
