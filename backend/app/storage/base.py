"""Abstract interface for object storage backends."""

from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Abstract interface defining required storage operations."""

    @abstractmethod
    def generate_presigned_upload_url(
        self, file_key: str, content_type: str, expires_in: int = 3600
    ) -> str:
        """Generate a presigned PUT URL for direct client upload."""
        pass

    @abstractmethod
    def generate_presigned_download_url(
        self, file_key: str, expires_in: int = 3600
    ) -> str:
        """Generate a presigned GET URL for temporary private download."""
        pass

    @abstractmethod
    def get_public_url(self, file_key: str) -> str:
        """Return the direct public URL for a file in the bucket."""
        pass

    @abstractmethod
    async def file_exists(self, file_key: str) -> bool:
        """Check whether an object exists in the storage bucket."""
        pass

    @abstractmethod
    async def get_file_metadata(self, file_key: str) -> dict | None:
        """Retrieve object metadata (e.g. content_length, content_type) from storage."""
        pass

    @abstractmethod
    async def delete_file(self, file_key: str) -> bool:
        """Delete an object from the storage bucket."""
        pass
