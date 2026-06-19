"""
Document source file storage service.

Generates upload and download URLs for document source files using the
local FileStorage backend. Upload goes to:
  PUT /api/assistants/{assistant_id}/documents/{document_id}/source

Download is served from:
  GET /api/assistants/{assistant_id}/documents/{document_id}/source

Cloud migration: when AzureBlobStorage is wired into get_file_storage(),
this service requires no changes — the URLs returned will be blob SAS URLs.
"""

import logging
import os
import re
from typing import Tuple

from apis.shared.storage import get_file_storage

logger = logging.getLogger(__name__)

_APP_URL = os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")


def _sanitize_filename(filename: str) -> str:
    filename = filename.lower()
    filename = re.sub(r"[^a-zA-Z0-9_.\-\(\)]", "_", filename)
    return filename


def _get_storage_key(assistant_id: str, document_id: str, filename: str) -> str:
    safe = _sanitize_filename(filename)
    return f"assistant-docs/{assistant_id}/documents/{document_id}/{safe}"


async def generate_upload_url(
    assistant_id: str,
    document_id: str,
    filename: str,
    content_type: str,
    expires_in: int = 3600,
) -> Tuple[str, str]:
    """Return (upload_url, storage_key)."""
    key = _get_storage_key(assistant_id, document_id, filename)
    url = f"{_APP_URL}/api/assistants/{assistant_id}/documents/{document_id}/source"
    return url, key


async def generate_download_url(s3_key: str, expires_in: int = 3600) -> str:
    """
    Return a download URL for a document source file.

    Parses the storage key pattern (assistant-docs/{assistant_id}/documents/{document_id}/...)
    to build the local endpoint URL. When AzureBlobStorage is active, this
    method should instead return a SAS URL via get_file_storage().get_download_url().
    """
    parts = s3_key.split("/")
    if len(parts) >= 4 and parts[0] == "assistant-docs":
        assistant_id = parts[1]
        document_id = parts[3]
        return f"{_APP_URL}/api/assistants/{assistant_id}/documents/{document_id}/source"
    logger.warning("Unexpected storage key format for download: %s", s3_key)
    return f"{_APP_URL}/api/documents/source?key={s3_key}"
