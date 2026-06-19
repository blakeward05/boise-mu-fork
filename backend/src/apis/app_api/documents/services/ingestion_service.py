"""
In-process document ingestion pipeline (replaces Lambda).

Called as a FastAPI BackgroundTask after the client confirms the upload.
Reads the source file from FileStorage, chunks via Docling/CSV, embeds,
stores in the VectorStore, and updates document status throughout.

Status progression: uploading → chunking → embedding → complete
                    (any stage) → failed
"""

import asyncio
import logging
import os
from typing import List, Optional

from apis.shared.storage import get_file_storage
from apis.shared.embeddings import get_embedding_model
from apis.shared.vector_store import get_vector_store
from apis.app_api.documents.services.document_service import update_document_status

logger = logging.getLogger(__name__)

_MAX_DOCUMENT_BYTES = int(os.environ.get("DOCUMENT_MAX_SIZE_BYTES", 20 * 1024 * 1024))  # 20 MB


async def ingest_document(
    *,
    assistant_id: str,
    document_id: str,
    storage_key: str,
    filename: str,
    content_type: str,
) -> None:
    """
    Full ingestion pipeline: read → chunk → embed → store → mark complete.

    Called as a background task; never raises (failures are caught and stored
    as document status='failed' so the frontend can surface them).
    """
    try:
        await _run_ingestion(
            assistant_id=assistant_id,
            document_id=document_id,
            storage_key=storage_key,
            filename=filename,
            content_type=content_type,
        )
    except Exception as exc:
        logger.error(
            "Ingestion failed for document %s: %s", document_id, exc, exc_info=True
        )
        await _fail(assistant_id, document_id, str(exc))


async def _run_ingestion(
    *,
    assistant_id: str,
    document_id: str,
    storage_key: str,
    filename: str,
    content_type: str,
) -> None:
    # --- Phase 1: Read source file ---
    logger.info("Ingestion starting: document=%s assistant=%s", document_id, assistant_id)
    storage = get_file_storage()

    try:
        raw_bytes = await storage.read(storage_key)
    except Exception as exc:
        raise RuntimeError(f"Failed to read source file from storage: {exc}") from exc

    if len(raw_bytes) > _MAX_DOCUMENT_BYTES:
        raise ValueError(
            f"Document too large: {len(raw_bytes)} bytes (max {_MAX_DOCUMENT_BYTES})"
        )

    # --- Phase 2: Chunk ---
    await update_document_status(assistant_id, document_id, "chunking")

    detected_type = _detect_mime_type(content_type, filename)
    chunks = await _chunk_document(raw_bytes, filename, detected_type)

    if not chunks:
        raise ValueError("No text could be extracted from the document")

    logger.info("Extracted %d chunks from document %s", len(chunks), document_id)

    # --- Phase 3: Embed ---
    await update_document_status(assistant_id, document_id, "embedding")

    # Validate / split oversized chunks before embedding
    chunks = _validate_and_split_chunks(chunks)

    embedding_model = get_embedding_model()
    embeddings = await embedding_model.embed_texts(chunks)

    # --- Phase 4: Store in vector store ---
    vector_store = get_vector_store()
    await vector_store.add_chunks(
        assistant_id=assistant_id,
        document_id=document_id,
        chunks=chunks,
        embeddings=embeddings,
        source_filename=filename,
    )

    # --- Phase 5: Mark complete ---
    await update_document_status(
        assistant_id=assistant_id,
        document_id=document_id,
        status="complete",
        chunk_count=len(chunks),
        vector_store_id="chroma" if os.environ.get("DATABASE_URL") else "s3-vectors",
    )

    logger.info(
        "Ingestion complete: document=%s chunks=%d", document_id, len(chunks)
    )


async def _chunk_document(raw_bytes: bytes, filename: str, content_type: str) -> List[str]:
    """Route to the appropriate chunker and return a flat list of text strings."""
    if content_type == "text/csv" or filename.lower().endswith(".csv"):
        return await _chunk_csv(raw_bytes, filename)
    return await _chunk_docling(raw_bytes, filename, content_type)


async def _chunk_docling(raw_bytes: bytes, filename: str, content_type: str) -> List[str]:
    """Run Docling in a thread pool to avoid blocking the event loop."""
    from apis.app_api.documents.ingestion.processors.docling_processor import process_with_docling
    # process_with_docling is itself async (uses run_in_executor internally)
    return await process_with_docling(raw_bytes, content_type, filename)


async def _chunk_csv(raw_bytes: bytes, filename: str) -> List[str]:
    from apis.app_api.documents.ingestion.processors.csv_chunker import chunk_csv

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: chunk_csv(raw_bytes))


def _ext(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return ext or ".bin"


def _detect_mime_type(content_type: Optional[str], filename: str) -> str:
    if content_type and content_type not in ("application/octet-stream", "binary/octet-stream"):
        return content_type
    ext = os.path.splitext(filename)[1].lower()
    _map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".csv": "text/csv",
        ".html": "text/html",
        ".htm": "text/html",
    }
    return _map.get(ext, content_type or "application/octet-stream")


def _validate_and_split_chunks(chunks: List[str], max_chars: int = 6000) -> List[str]:
    """
    Fallback hard-split for chunks that are too long to embed safely.
    Docling normally handles this but this is a safety net.
    """
    result: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            # Split on paragraph boundaries first
            paragraphs = chunk.split("\n\n")
            buf = ""
            for para in paragraphs:
                if len(buf) + len(para) + 2 <= max_chars:
                    buf = (buf + "\n\n" + para).strip()
                else:
                    if buf:
                        result.append(buf)
                    buf = para[:max_chars]
            if buf:
                result.append(buf)
    return result


async def _fail(assistant_id: str, document_id: str, reason: str) -> None:
    try:
        await update_document_status(
            assistant_id=assistant_id,
            document_id=document_id,
            status="failed",
            error_message="Document processing failed. Please try uploading again.",
            error_details=reason[:1000],
        )
    except Exception as exc:
        logger.error("Failed to mark document %s as failed: %s", document_id, exc)
