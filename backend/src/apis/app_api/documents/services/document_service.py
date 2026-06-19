"""
Document service — MongoDB implementation (replaces DynamoDB).

Collection: assistant_documents
_id: "{assistant_id}#{document_id}"

Indexes (ensure these in startup or migration):
  {assistant_id: 1, document_id: 1}  unique
  {assistant_id: 1, status: 1}
  {deleted_at: 1}  TTL (expireAfterSeconds: 604800 = 7 days)
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from apis.shared.database.connection import get_database
from apis.app_api.documents.models import Document, DocumentStatus

logger = logging.getLogger(__name__)

COLLECTION = "assistant_documents"
STALE_PROCESSING_TIMEOUT_MINUTES = 20
PROCESSING_STATES: set = {"uploading", "chunking", "embedding"}


def _generate_document_id() -> str:
    return f"DOC-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _doc_id(assistant_id: str, document_id: str) -> str:
    return f"{assistant_id}#{document_id}"


def _doc_to_model(item: dict) -> Optional[Document]:
    try:
        return Document.model_validate({
            "documentId": item["document_id"],
            "assistantId": item["assistant_id"],
            "filename": item["filename"],
            "contentType": item["content_type"],
            "sizeBytes": item["size_bytes"],
            "s3Key": item["storage_key"],
            "vectorStoreId": item.get("vector_store_id"),
            "status": item["status"],
            "errorMessage": item.get("error_message"),
            "errorDetails": item.get("error_details"),
            "chunkCount": item.get("chunk_count"),
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        })
    except Exception as e:
        logger.warning("Failed to parse document item: %s", e)
        return None


def _is_document_stale(document: Document) -> bool:
    if document.status not in PROCESSING_STATES:
        return False
    try:
        updated_str = document.updated_at.rstrip("Z")
        updated_at = datetime.fromisoformat(updated_str).replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - updated_at).total_seconds() / 60
        return elapsed > STALE_PROCESSING_TIMEOUT_MINUTES
    except (ValueError, AttributeError):
        return False


async def _auto_fail_stale(document: Document) -> Document:
    logger.warning("Auto-failing stale document %s (status=%s)", document.document_id, document.status)
    updated = await update_document_status(
        assistant_id=document.assistant_id,
        document_id=document.document_id,
        status="failed",
        error_message="Processing timed out. The document may need to be re-uploaded.",
        error_details=f'Document was stuck in "{document.status}" state since {document.updated_at}',
    )
    return updated if updated else document


async def create_document(
    assistant_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    s3_key: str,
    document_id: Optional[str] = None,
) -> Document:
    if not document_id:
        document_id = _generate_document_id()
    now = _now_iso()

    doc = {
        "_id": _doc_id(assistant_id, document_id),
        "assistant_id": assistant_id,
        "document_id": document_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "storage_key": s3_key,
        "status": "uploading",
        "created_at": now,
        "updated_at": now,
    }

    db = get_database()
    await db[COLLECTION].insert_one(doc)
    logger.info("Created document %s for assistant %s", document_id, assistant_id)
    return _doc_to_model(doc)


async def get_document(
    assistant_id: str,
    document_id: str,
    owner_id: str,
) -> Optional[Document]:
    from apis.shared.assistants.service import get_assistant

    assistant = await get_assistant(assistant_id, owner_id)
    if not assistant:
        logger.warning("Access denied: assistant %s not owned by user %s", assistant_id, owner_id)
        return None

    db = get_database()
    item = await db[COLLECTION].find_one({"_id": _doc_id(assistant_id, document_id)})
    if not item:
        return None

    document = _doc_to_model(item)
    if not document:
        return None

    if document.status == "deleting":
        return None

    if _is_document_stale(document):
        document = await _auto_fail_stale(document)

    return document


async def update_document_status(
    assistant_id: str,
    document_id: str,
    status: DocumentStatus,
    vector_store_id: Optional[str] = None,
    chunk_count: Optional[int] = None,
    error_message: Optional[str] = None,
    error_details: Optional[str] = None,
    table_name: Optional[str] = None,  # ignored (DynamoDB compat param)
) -> Optional[Document]:
    db = get_database()
    now = _now_iso()

    update_fields: dict = {"status": status, "updated_at": now}
    if chunk_count is not None:
        update_fields["chunk_count"] = chunk_count
    if vector_store_id is not None:
        update_fields["vector_store_id"] = vector_store_id

    unset_fields: dict = {}
    if status == "failed":
        if error_message is not None:
            update_fields["error_message"] = error_message
        if error_details is not None:
            update_fields["error_details"] = error_details
    else:
        unset_fields = {"error_message": "", "error_details": ""}

    op: dict = {"$set": update_fields}
    if unset_fields:
        op["$unset"] = unset_fields

    result = await db[COLLECTION].find_one_and_update(
        {"_id": _doc_id(assistant_id, document_id)},
        op,
        return_document=True,
    )

    if not result:
        return None
    return _doc_to_model(result)


async def list_assistant_documents(
    assistant_id: str,
    owner_id: str,
    limit: Optional[int] = None,
    next_token: Optional[str] = None,
) -> Tuple[List[Document], Optional[str]]:
    from apis.shared.assistants.service import get_assistant

    assistant = await get_assistant(assistant_id, owner_id)
    if not assistant:
        logger.warning("Access denied: assistant %s not owned by %s", assistant_id, owner_id)
        return [], None

    db = get_database()
    query: dict = {"assistant_id": assistant_id, "status": {"$ne": "deleting"}}

    cursor = db[COLLECTION].find(query).sort("created_at", -1)
    if limit and limit > 0:
        cursor = cursor.limit(limit + 1)

    items = await cursor.to_list(length=limit + 1 if limit else None)

    next_page_token: Optional[str] = None
    if limit and len(items) > limit:
        items = items[:limit]
        # Simple cursor: last document's created_at as offset sentinel
        import base64, json
        next_page_token = base64.b64encode(
            json.dumps({"after": items[-1]["created_at"]}).encode()
        ).decode()

    documents: List[Document] = []
    for item in items:
        doc = _doc_to_model(item)
        if not doc:
            continue
        if _is_document_stale(doc):
            doc = await _auto_fail_stale(doc)
        documents.append(doc)

    logger.info("Listed %d documents for assistant %s", len(documents), assistant_id)
    return documents, next_page_token


async def soft_delete_document(
    assistant_id: str,
    document_id: str,
    owner_id: str,
    ttl_days: int = 7,
) -> Optional[Document]:
    from apis.shared.assistants.service import get_assistant

    assistant = await get_assistant(assistant_id, owner_id)
    if not assistant:
        return None

    db = get_database()
    deleted_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

    result = await db[COLLECTION].find_one_and_update(
        {"_id": _doc_id(assistant_id, document_id)},
        {"$set": {"status": "deleting", "updated_at": _now_iso(), "deleted_at": deleted_at}},
        return_document=True,
    )

    if not result:
        return None
    return _doc_to_model(result)


async def hard_delete_document(assistant_id: str, document_id: str) -> bool:
    db = get_database()
    result = await db[COLLECTION].delete_one({"_id": _doc_id(assistant_id, document_id)})
    deleted = result.deleted_count > 0
    if deleted:
        logger.info("Hard-deleted document %s for assistant %s", document_id, assistant_id)
    return deleted


async def delete_document(assistant_id: str, document_id: str, owner_id: str) -> bool:
    """Hard-delete with ownership check (used in tests)."""
    from apis.shared.assistants.service import get_assistant
    if not await get_assistant(assistant_id, owner_id):
        return False
    return await hard_delete_document(assistant_id, document_id)


async def batch_soft_delete_documents(
    assistant_id: str,
    document_ids: List[str],
    ttl_days: int = 7,
) -> int:
    if not document_ids:
        return 0

    db = get_database()
    deleted_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    ids = [_doc_id(assistant_id, did) for did in document_ids]

    result = await db[COLLECTION].update_many(
        {"_id": {"$in": ids}},
        {"$set": {"status": "deleting", "updated_at": _now_iso(), "deleted_at": deleted_at}},
    )
    count = result.modified_count
    logger.info("Batch soft-deleted %d/%d documents for assistant %s", count, len(document_ids), assistant_id)
    return count
