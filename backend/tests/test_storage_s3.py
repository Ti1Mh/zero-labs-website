"""Unit tests for S3 / MinIO storage implementation."""

import asyncio
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from app.core.config import Settings
from app.storage.s3 import S3Storage
from app.uploads.service import generate_secure_file_key


def test_secure_file_key_sanitization():
    """Verify generated keys are isolated, collision-resistant, and sanitized."""
    key = generate_secure_file_key(owner_id=42, filename="../../malicious/evil.php.PNG")
    assert key.startswith("uploads/42/")
    assert key.endswith(".png")
    assert "evil" not in key
    assert ".." not in key
    assert "/" not in key.split("/")[-1]  # The file part is just uuid.ext


def test_secure_file_key_handles_no_extension():
    """Verify keys without extensions fallback safely."""
    key = generate_secure_file_key(owner_id=1, filename="unknown_file")
    assert key.endswith(".bin")


def test_generate_presigned_upload_url():
    """Verify S3Storage generates valid presigned PUT URL."""
    settings = Settings(
        database_url="postgresql+asyncpg://postgres:postgres@127.0.0.1:5434/test",
        secret_key="secret" * 8,
        s3_endpoint_url="http://127.0.0.1:9000",
        s3_bucket_name="social-publish",
        s3_access_key="test_key",
        s3_secret_key="test_secret",
    )
    storage = S3Storage(settings=settings)

    url = storage.generate_presigned_upload_url(
        file_key="uploads/1/test.png",
        content_type="image/png",
        expires_in=1800,
    )

    assert "http://127.0.0.1:9000/social-publish/uploads/1/test.png" in url
    assert "X-Amz-Signature=" in url
    assert "X-Amz-Expires=1800" in url


def test_generate_presigned_download_url():
    """Verify S3Storage generates valid presigned GET URL."""
    settings = Settings(
        database_url="postgresql+asyncpg://postgres:postgres@127.0.0.1:5434/test",
        secret_key="secret" * 8,
        s3_endpoint_url="http://127.0.0.1:9000",
        s3_bucket_name="social-publish",
        s3_access_key="test_key",
        s3_secret_key="test_secret",
    )
    storage = S3Storage(settings=settings)

    url = storage.generate_presigned_download_url(
        file_key="uploads/1/test.png",
        expires_in=300,
    )

    assert "http://127.0.0.1:9000/social-publish/uploads/1/test.png" in url
    assert "X-Amz-Signature=" in url
    assert "X-Amz-Expires=300" in url


def test_file_exists_and_metadata_sync_helpers():
    """Verify threadpool-wrapped existence and metadata checks."""
    async def _run():
        storage = S3Storage()
        storage.client = MagicMock()

        # 1) Object exists
        storage.client.head_object.return_value = {
            "ContentLength": 1024,
            "ContentType": "image/png",
            "ETag": '"abc123etag"',
        }
        exists = await storage.file_exists("test.png")
        assert exists is True

        meta = await storage.get_file_metadata("test.png")
        assert meta["content_length"] == 1024
        assert meta["etag"] == "abc123etag"

        # 2) Object does not exist (404)
        error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
        storage.client.head_object.side_effect = ClientError(error_response, "HeadObject")

        not_exists = await storage.file_exists("missing.png")
        assert not_exists is False
        assert await storage.get_file_metadata("missing.png") is None

    asyncio.run(_run())


def test_delete_file_sync_helper():
    """Verify delete_file calls S3 delete_object."""
    async def _run():
        storage = S3Storage()
        storage.client = MagicMock()

        deleted = await storage.delete_file("test.png")
        assert deleted is True
        storage.client.delete_object.assert_called_once_with(
            Bucket=storage.bucket, Key="test.png"
        )

    asyncio.run(_run())
