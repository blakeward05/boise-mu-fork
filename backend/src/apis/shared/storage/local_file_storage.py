"""
Local filesystem FileStorage implementation.

Files are stored under LOCAL_STORAGE_PATH (default: ./data/files).
The upload URL points to the app API's own PUT endpoint so the browser
can upload directly without a separate object-storage service.

Azure Blob Storage migration: swap this class for AzureBlobStorage —
the interface is identical so all callers are unchanged.
"""

import logging
import os
from pathlib import Path

import aiofiles

from .file_storage import FileStorage

logger = logging.getLogger(__name__)


class LocalFileStorage(FileStorage):
    """Stores files on the local filesystem under LOCAL_STORAGE_PATH."""

    def __init__(self, base_path: str | None = None, app_url: str | None = None) -> None:
        self._base = Path(
            base_path
            or os.environ.get("LOCAL_STORAGE_PATH", "./data/files")
        ).resolve()
        # App URL used to construct the upload endpoint URL returned to clients.
        # Should match what the frontend knows as appApiUrl.
        self._app_url = (
            app_url
            or os.environ.get("APP_URL", "http://localhost:8000")
        ).rstrip("/")

    def _full_path(self, key: str) -> Path:
        # Prevent path traversal: resolve inside base dir
        target = (self._base / key).resolve()
        if not str(target).startswith(str(self._base)):
            raise ValueError(f"Storage key escapes base directory: {key!r}")
        return target

    async def get_upload_url(
        self,
        upload_id: str,
        key: str,
        content_type: str,
        expires_in: int = 900,
    ) -> str:
        """Return a PUT URL pointing at the local upload endpoint."""
        return f"{self._app_url}/files/{upload_id}/content"

    async def read(self, key: str) -> bytes:
        path = self._full_path(key)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def write(self, key: str, data: bytes) -> None:
        path = self._full_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        logger.debug("Wrote %d bytes to %s", len(data), path)

    async def delete(self, key: str) -> None:
        path = self._full_path(key)
        try:
            path.unlink()
            logger.debug("Deleted %s", path)
        except FileNotFoundError:
            pass

    async def exists(self, key: str) -> bool:
        try:
            return self._full_path(key).is_file()
        except ValueError:
            return False
