"""Database model for uploaded media files."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MediaFile(Base):
    """An uploaded file stored in object storage (MinIO/S3)."""

    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    file_key: Mapped[str] = mapped_column(
        String(500), unique=True, index=True, nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    public_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
