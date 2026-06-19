"""
FileStorage abstract interface.

Implementations:
  LocalFileStorage  – local filesystem (default for local dev)
  S3FileStorage     – AWS S3 (cloud deployment)

Azure Blob Storage is the cloud-ready swap target: implement AzureBlobStorage
with the same interface and change get_file_storage() to return it.

Key design:
  - `key` is a provider-agnostic storage path (e.g. user-files/{uid}/{session}/{id}/{file})
  - upload_url is whatever the client should PUT/POST to (S3 presigned URL or local endpoint)
  - All I/O methods are async so callers don't need to special-case the backend
"""

from abc import ABC, abstractmethod


class FileStorage(ABC):
    """Abstract file storage backend."""

    @abstractmethod
    async def get_upload_url(
        self,
        upload_id: str,
        key: str,
        content_type: str,
        expires_in: int = 900,
    ) -> str:
        """Return a URL the HTTP client should PUT the raw file bytes to."""

    @abstractmethod
    async def read(self, key: str) -> bytes:
        """Return the raw bytes stored at `key`."""

    @abstractmethod
    async def write(self, key: str, data: bytes) -> None:
        """Store `data` at `key` (used by the local upload endpoint)."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete the object at `key`. Silently succeeds if not found."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if `key` exists in storage."""
