"""Database model for idempotent callback processing from external bots."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProcessedCallbackEvent(Base):
    """Immutable audit ledger of processed callback events to guarantee idempotency."""

    __tablename__ = "processed_callback_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("content_jobs.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    platform_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
