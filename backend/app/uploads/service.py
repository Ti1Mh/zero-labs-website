"""Upload business logic: key generation, presigned URL allocation, and confirmation."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from app.storage.base import StorageBackend
from app.uploads.models import MediaFile
from app.uploads.schemas import (
    ALLOWED_CONTENT_TYPES,
    MAX_FILE_SIZE_BYTES,
    ConfirmUploadRequest,
    PresignUploadRequest,
    PresignUploadResponse,
)


def generate_secure_file_key(owner_id: int, filename: str) -> str:
    """Generate an isolated, collision-resistant S3 object key."""
    suffix = Path(filename).suffix.lower()
    # Sanitize suffix: keep only alphanumeric chars and dot
    clean_suffix = "".join(c for c in suffix if c.isalnum() or c == ".")
    if not clean_suffix or len(clean_suffix) > 10:
        clean_suffix = ".bin"

    now = datetime.now(timezone.utc)
    unique_id = uuid4().hex
    return f"uploads/{owner_id}/{now.year}/{now.month:02d}/{unique_id}{clean_suffix}"


def validate_upload_request(payload: PresignUploadRequest) -> None:
    """Validate content type and file size."""
    normalized_type = payload.content_type.lower().strip()
    if normalized_type not in ALLOWED_CONTENT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_CONTENT_TYPES))
        raise InvalidInputError(
            f"نوع فایل {payload.content_type} مجاز نیست. فرمت‌های مجاز: {allowed}"
        )

    if payload.file_size > MAX_FILE_SIZE_BYTES:
        raise InvalidInputError(
            f"حجم فایل نمی‌تواند بیشتر از {MAX_FILE_SIZE_BYTES // (1024 * 1024)} مگابایت باشد."
        )


async def create_presigned_upload(
    storage: StorageBackend,
    owner_id: int,
    payload: PresignUploadRequest,
    expires_in: int = 3600,
) -> PresignUploadResponse:
    """Validate request and generate presigned PUT URL."""
    validate_upload_request(payload)
    file_key = generate_secure_file_key(owner_id, payload.filename)

    upload_url = storage.generate_presigned_upload_url(
        file_key=file_key,
        content_type=payload.content_type,
        expires_in=expires_in,
    )
    public_url = storage.get_public_url(file_key)

    return PresignUploadResponse(
        upload_url=upload_url,
        file_key=file_key,
        expires_in=expires_in,
        public_url=public_url,
    )


async def confirm_upload(
    db: AsyncSession,
    storage: StorageBackend,
    owner_id: int,
    payload: ConfirmUploadRequest,
) -> MediaFile:
    """Verify file exists in storage and record it in database."""
    # 1) Idempotency guard: return existing record if already confirmed
    stmt = select(MediaFile).where(MediaFile.file_key == payload.file_key)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    # 2) Verify presence in object storage
    exists = await storage.file_exists(payload.file_key)
    if not exists:
        raise NotFoundError(
            "فایل در فضای ذخیره‌سازی یافت نشد. لطفاً ابتدا آپلود را تکمیل کنید."
        )

    # 3) Read actual metadata from storage
    metadata = await storage.get_file_metadata(payload.file_key)
    file_size = metadata.get("content_length") if metadata else 0

    bucket_name = getattr(storage, "bucket", "social-publish")
    public_url = storage.get_public_url(payload.file_key)

    media = MediaFile(
        owner_id=owner_id,
        file_key=payload.file_key,
        original_filename=payload.original_filename,
        content_type=payload.content_type,
        file_size=file_size or 0,
        bucket=bucket_name,
        public_url=public_url,
        content_job_id=payload.content_job_id,
    )
    db.add(media)
    await db.flush()
    return media


async def list_media_files(
    db: AsyncSession, owner_id: int, limit: int = 20, offset: int = 0
) -> tuple[list[MediaFile], int]:
    """Return paginated list of media files for a team."""
    stmt = (
        select(MediaFile)
        .where(MediaFile.owner_id == owner_id)
        .order_by(MediaFile.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count(MediaFile.id)).where(MediaFile.owner_id == owner_id)

    res = await db.execute(stmt)
    items = list(res.scalars().all())

    count_res = await db.execute(count_stmt)
    total = count_res.scalar_one() or 0

    return items, total


async def delete_media_file(
    db: AsyncSession, storage: StorageBackend, owner_id: int, file_id: int
) -> bool:
    """Delete a media file from DB and storage."""
    stmt = select(MediaFile).where(MediaFile.id == file_id, MediaFile.owner_id == owner_id)
    res = await db.execute(stmt)
    media = res.scalar_one_or_none()
    if media is None:
        raise NotFoundError("فایل یافت نشد.")

    await storage.delete_file(media.file_key)
    await db.delete(media)
    await db.flush()
    return True
