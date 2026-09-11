"""Pydantic schemas for file upload endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Supported MIME types for social publishing
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/quicktime",
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


class PresignUploadRequest(BaseModel):
    """Client request to generate a presigned PUT upload URL."""

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=100)
    file_size: int = Field(ge=1, le=MAX_FILE_SIZE_BYTES, description="File size in bytes")


class PresignUploadResponse(BaseModel):
    """Presigned PUT URL and allocated object key returned to client."""

    upload_url: str
    file_key: str
    expires_in: int
    public_url: str


class ConfirmUploadRequest(BaseModel):
    """Client notification that upload to S3/MinIO completed successfully."""

    file_key: str = Field(min_length=5, max_length=500)
    original_filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=100)
    content_job_id: int | None = None


class MediaFileOut(BaseModel):
    """Public representation of a confirmed media file."""

    id: int
    file_key: str
    original_filename: str
    content_type: str
    file_size: int
    public_url: str
    content_job_id: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MediaFileListResponse(BaseModel):
    """Paginated or listed media files."""

    items: list[MediaFileOut]
    total: int
