"""Storage dependency injection factory."""

from functools import lru_cache

from app.core.config import get_settings
from app.storage.base import StorageBackend
from app.storage.s3 import S3Storage


@lru_cache
def get_storage() -> StorageBackend:
    """Return a singleton storage backend instance."""
    settings = get_settings()
    return S3Storage(settings=settings)
