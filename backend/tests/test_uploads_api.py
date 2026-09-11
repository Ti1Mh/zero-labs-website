"""Unit tests for uploads business logic and endpoints."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import InvalidInputError, NotFoundError
from app.uploads.models import MediaFile
from app.uploads.schemas import (
    ConfirmUploadRequest,
    PresignUploadRequest,
)
from app.uploads.service import (
    confirm_upload,
    create_presigned_upload,
    delete_media_file,
    validate_upload_request,
)


def test_validate_upload_request_content_type():
    """Verify MIME whitelist enforcement."""
    # Valid types
    validate_upload_request(PresignUploadRequest(
        filename="photo.jpg", content_type="image/jpeg", file_size=1024
    ))
    validate_upload_request(PresignUploadRequest(
        filename="video.mp4", content_type="video/mp4", file_size=1024
    ))

    # Invalid type
    with pytest.raises(InvalidInputError) as exc:
        validate_upload_request(PresignUploadRequest(
            filename="script.sh", content_type="application/x-sh", file_size=1024
        ))
    assert "مجاز نیست" in str(exc.value)


def test_validate_upload_request_file_size():
    """Verify file size cap enforcement via schema validation."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PresignUploadRequest(
            filename="large.mp4", content_type="video/mp4", file_size=100 * 1024 * 1024
        )


def test_create_presigned_upload():
    """Verify create_presigned_upload returns valid response structure."""
    async def _run():
        storage = MagicMock()
        storage.generate_presigned_upload_url.return_value = "http://minio/upload?sig=abc"
        storage.get_public_url.return_value = "http://minio/social-publish/test.png"

        res = await create_presigned_upload(
            storage=storage,
            owner_id=5,
            payload=PresignUploadRequest(
                filename="banner.png",
                content_type="image/png",
                file_size=2048,
            ),
        )
        assert res.upload_url == "http://minio/upload?sig=abc"
        assert res.file_key.startswith("uploads/5/")
        assert res.file_key.endswith(".png")
        assert res.expires_in == 3600

    asyncio.run(_run())


def test_confirm_upload_raises_when_file_not_found():
    """Verify confirm_upload raises NotFoundError if file is missing in storage."""
    async def _run():
        db = AsyncMock()
        db.execute = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        storage = AsyncMock()
        storage.file_exists.return_value = False

        with pytest.raises(NotFoundError) as exc:
            await confirm_upload(
                db=db,
                storage=storage,
                owner_id=5,
                payload=ConfirmUploadRequest(
                    file_key="uploads/5/missing.png",
                    original_filename="missing.png",
                    content_type="image/png",
                ),
            )
        assert "در فضای ذخیره‌سازی یافت نشد" in str(exc.value)

    asyncio.run(_run())


def test_confirm_upload_success_and_idempotency():
    """Verify confirm_upload creates MediaFile and is idempotent."""
    async def _run():
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        # 1) First call: file does not exist in DB yet
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        storage = AsyncMock()
        storage.file_exists.return_value = True
        storage.get_file_metadata.return_value = {"content_length": 5000}
        storage.get_public_url.return_value = "http://minio/social-publish/uploads/5/photo.jpg"

        media = await confirm_upload(
            db=db,
            storage=storage,
            owner_id=5,
            payload=ConfirmUploadRequest(
                file_key="uploads/5/photo.jpg",
                original_filename="photo.jpg",
                content_type="image/jpeg",
            ),
        )
        assert media.file_key == "uploads/5/photo.jpg"
        assert media.file_size == 5000
        assert media.owner_id == 5

        # 2) Second call: already in DB -> returns existing record
        mock_result_existing = MagicMock()
        mock_result_existing.scalar_one_or_none.return_value = media
        db.execute = AsyncMock(return_value=mock_result_existing)

        media2 = await confirm_upload(
            db=db,
            storage=storage,
            owner_id=5,
            payload=ConfirmUploadRequest(
                file_key="uploads/5/photo.jpg",
                original_filename="photo.jpg",
                content_type="image/jpeg",
            ),
        )
        assert media2 == media

    asyncio.run(_run())


def test_delete_media_file():
    """Verify delete_media_file removes from storage and DB."""
    async def _run():
        db = AsyncMock()
        db.delete = AsyncMock()
        db.flush = AsyncMock()

        existing_media = MediaFile(
            id=1,
            owner_id=5,
            file_key="uploads/5/photo.jpg",
            original_filename="photo.jpg",
            content_type="image/jpeg",
            file_size=1000,
            bucket="social-publish",
            public_url="http://minio/photo.jpg",
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_media
        db.execute.return_value = mock_result

        storage = AsyncMock()
        storage.delete_file.return_value = True

        res = await delete_media_file(db=db, storage=storage, owner_id=5, file_id=1)
        assert res is True
        storage.delete_file.assert_awaited_once_with("uploads/5/photo.jpg")
        db.delete.assert_awaited_once_with(existing_media)

    asyncio.run(_run())
