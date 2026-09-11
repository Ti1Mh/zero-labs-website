"""S3 / MinIO storage backend implementation."""

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.storage.base import StorageBackend


class S3Storage(StorageBackend):
    """S3-compatible storage implementation for MinIO, AWS S3, Cloudflare R2, etc."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize boto3 S3 client with configured endpoint and credentials."""
        self.settings = settings or get_settings()
        self.bucket = self.settings.s3_bucket_name
        self.endpoint_url = self.settings.s3_endpoint_url
        self.public_url_base = self.settings.s3_public_url or f"{self.endpoint_url.rstrip('/')}/{self.bucket}"

        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            region_name=self.settings.s3_region_name,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        )

    def generate_presigned_upload_url(
        self, file_key: str, content_type: str, expires_in: int = 3600
    ) -> str:
        """Generate a presigned PUT URL for direct client upload (HMAC local computation)."""
        return self.client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": self.bucket,
                "Key": file_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

    def generate_presigned_download_url(
        self, file_key: str, expires_in: int = 3600
    ) -> str:
        """Generate a presigned GET URL for temporary private download."""
        return self.client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": self.bucket,
                "Key": file_key,
            },
            ExpiresIn=expires_in,
        )

    def get_public_url(self, file_key: str) -> str:
        """Return the direct public URL for a file."""
        return f"{self.public_url_base.rstrip('/')}/{file_key.lstrip('/')}"

    def _head_object_sync(self, file_key: str) -> dict | None:
        """Synchronous head_object helper."""
        try:
            return self.client.head_object(Bucket=self.bucket, Key=file_key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    async def file_exists(self, file_key: str) -> bool:
        """Check whether an object exists without blocking the event loop."""
        resp = await run_in_threadpool(self._head_object_sync, file_key)
        return resp is not None

    async def get_file_metadata(self, file_key: str) -> dict | None:
        """Retrieve object metadata without blocking the event loop."""
        resp = await run_in_threadpool(self._head_object_sync, file_key)
        if resp is None:
            return None
        return {
            "content_length": resp.get("ContentLength"),
            "content_type": resp.get("ContentType"),
            "etag": resp.get("ETag", "").strip('"'),
            "last_modified": resp.get("LastModified"),
        }

    def _delete_object_sync(self, file_key: str) -> bool:
        """Synchronous delete_object helper."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=file_key)
            return True
        except ClientError:
            return False

    async def delete_file(self, file_key: str) -> bool:
        """Delete an object from the bucket without blocking the event loop."""
        return await run_in_threadpool(self._delete_object_sync, file_key)
