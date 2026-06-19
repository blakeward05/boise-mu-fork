"""
File Resolver Service

Resolves file upload IDs to FileContent objects with base64-encoded bytes.
Used by chat endpoints to fetch files before passing to the agent.

Storage backend is provided by get_file_storage() — local filesystem or S3
depending on DATABASE_URL, with no changes required at the call site.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import List, Optional

from apis.shared.storage import get_file_storage, FileStorage
from .repository import get_file_upload_repository
from .models import FileStatus

logger = logging.getLogger(__name__)


@dataclass
class ResolvedFileContent:
    """Resolved file content with base64-encoded bytes."""
    filename: str
    content_type: str
    bytes: str  # base64-encoded


class FileResolverError(Exception):
    """Error resolving file content."""


class FileResolver:
    """
    Resolves file upload IDs to ResolvedFileContent objects.

    Fetches metadata from MongoDB and raw bytes from the configured
    FileStorage backend, then base64-encodes for the agent.
    """

    def __init__(self, storage: Optional[FileStorage] = None) -> None:
        self._storage = storage or get_file_storage()
        self._file_repository = get_file_upload_repository()

    async def resolve_files(
        self,
        user_id: str,
        upload_ids: List[str],
        max_files: int = 5,
    ) -> List[ResolvedFileContent]:
        """Resolve a list of upload IDs to base64-encoded file content."""
        resolved: List[ResolvedFileContent] = []
        for upload_id in upload_ids[:max_files]:
            try:
                content = await self._resolve_single_file(user_id, upload_id)
                if content:
                    resolved.append(content)
            except Exception as exc:
                logger.warning("Failed to resolve file %s: %s", upload_id, exc)
        return resolved

    async def _resolve_single_file(
        self, user_id: str, upload_id: str
    ) -> Optional[ResolvedFileContent]:
        file_meta = await self._file_repository.get_file(user_id, upload_id)
        if not file_meta:
            logger.warning("File %s not found for user %s", upload_id, user_id)
            return None

        if file_meta.status != FileStatus.READY:
            logger.warning("File %s not ready: %s", upload_id, file_meta.status)
            return None

        try:
            raw_bytes = await self._storage.read(file_meta.s3_key)
        except Exception as exc:
            logger.error("Failed to read file %s from storage: %s", upload_id, exc)
            return None

        return ResolvedFileContent(
            filename=file_meta.filename,
            content_type=file_meta.mime_type,
            bytes=base64.b64encode(raw_bytes).decode("utf-8"),
        )


_resolver_instance: Optional[FileResolver] = None


def get_file_resolver() -> FileResolver:
    global _resolver_instance
    if _resolver_instance is None:
        _resolver_instance = FileResolver()
    return _resolver_instance
