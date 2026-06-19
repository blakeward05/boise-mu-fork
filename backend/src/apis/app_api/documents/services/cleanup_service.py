"""
Cleanup service for document resource deletion with retries.

Orchestrates deletion of vectors and stored source files with exponential
backoff. Uses VectorStore and FileStorage abstractions so it works with
both local (Chroma + filesystem) and cloud (S3 Vectors + S3) backends.

Never raises exceptions — all failures are logged and swallowed.
"""

import asyncio
import logging
import random
from typing import Optional

from apis.shared.storage import get_file_storage
from apis.shared.vector_store import get_vector_store

logger = logging.getLogger(__name__)


async def cleanup_document_resources(
    document_id: str,
    assistant_id: str,
    s3_key: str,
    chunk_count: Optional[int],
    max_retries: int = 3,
    base_delay: float = 0.5,
) -> bool:
    """
    Delete vectors and source file with exponential backoff retries.

    Phase 1: Delete vectors (deterministic if chunk_count provided, else filter scan).
    Phase 2: Delete source file from storage.
    Phases are independent — failure of one does not prevent the other.

    Returns True only when both phases succeed, then hard-deletes the DB record.
    On partial failure, leaves the record for TTL auto-expiry.

    Never raises.
    """
    try:
        vectors_deleted = await _delete_vectors_with_retries(
            document_id, chunk_count, max_retries, base_delay
        )
    except Exception as exc:
        logger.error("Unexpected error in vector deletion for %s: %s", document_id, exc, exc_info=True)
        vectors_deleted = False

    try:
        file_deleted = await _delete_file_with_retries(s3_key, max_retries, base_delay)
    except Exception as exc:
        logger.error("Unexpected error in file deletion for %s: %s", document_id, exc, exc_info=True)
        file_deleted = False

    all_succeeded = vectors_deleted and file_deleted

    if all_succeeded:
        try:
            from apis.app_api.documents.services.document_service import hard_delete_document
            await hard_delete_document(assistant_id, document_id)
        except Exception as exc:
            logger.error("Failed to hard-delete document %s: %s", document_id, exc, exc_info=True)
    else:
        logger.warning(
            "Cleanup incomplete for %s: vectors=%s file=%s. TTL will auto-expire.",
            document_id,
            vectors_deleted,
            file_deleted,
        )

    return all_succeeded


async def _delete_vectors_with_retries(
    document_id: str,
    chunk_count: Optional[int],
    max_retries: int,
    base_delay: float,
) -> bool:
    for attempt in range(max_retries):
        try:
            store = get_vector_store()
            await store.delete_document(document_id, chunk_count)
            return True
        except Exception as exc:
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
            logger.warning(
                "Vector deletion attempt %d/%d failed for %s: %s, retrying in %.2fs",
                attempt + 1, max_retries, document_id, exc, delay,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)

    logger.error("Vector deletion failed after %d attempts for %s", max_retries, document_id)
    return False


async def _delete_file_with_retries(
    storage_key: str,
    max_retries: int,
    base_delay: float,
) -> bool:
    for attempt in range(max_retries):
        try:
            storage = get_file_storage()
            await storage.delete(storage_key)
            return True
        except Exception as exc:
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
            logger.warning(
                "File deletion attempt %d/%d failed for %s: %s, retrying in %.2fs",
                attempt + 1, max_retries, storage_key, exc, delay,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)

    logger.error("File deletion failed after %d attempts for %s", max_retries, storage_key)
    return False


async def cleanup_assistant_documents(
    assistant_id: str,
    documents: list,
    max_retries: int = 3,
) -> tuple:
    """
    Bulk cleanup for assistant deletion. Processes documents concurrently.
    Returns (success_count, failure_count). Never raises.
    """
    if not documents:
        return (0, 0)

    try:
        results = await asyncio.gather(
            *(
                cleanup_document_resources(
                    document_id=doc.document_id,
                    assistant_id=assistant_id,
                    s3_key=doc.s3_key,
                    chunk_count=doc.chunk_count,
                    max_retries=max_retries,
                )
                for doc in documents
            ),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.error("Unexpected error in bulk cleanup for assistant %s: %s", assistant_id, exc, exc_info=True)
        return (0, len(documents))

    success_count = sum(1 for r in results if r is True)
    failure_count = len(results) - success_count
    logger.info(
        "Bulk cleanup for assistant %s: %d succeeded, %d failed out of %d documents",
        assistant_id, success_count, failure_count, len(documents),
    )
    return (success_count, failure_count)
