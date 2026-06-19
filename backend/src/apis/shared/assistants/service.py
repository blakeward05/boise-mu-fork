"""Assistant service layer — MongoDB-backed.

Two collections:
  assistants        — assistant metadata (one doc per assistant)
  assistant_shares  — share records (one doc per assistant+email pair)
"""

import base64
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .models import Assistant

logger = logging.getLogger(__name__)


def _generate_assistant_id() -> str:
    return f"ast-{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def _to_doc(assistant: Assistant) -> dict:
    doc = assistant.model_dump(by_alias=True, exclude_none=True)
    doc["_id"] = assistant.assistant_id
    return doc


def _from_doc(doc: dict) -> Assistant:
    doc.setdefault("assistantId", doc.get("_id", ""))
    return Assistant.model_validate(doc)


async def _get_db():
    from apis.shared.database import get_database
    return get_database()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

async def create_assistant_draft(owner_id: str, owner_name: str, name: Optional[str] = None) -> Assistant:
    now = _now()
    assistant = Assistant(
        assistant_id=_generate_assistant_id(),
        owner_id=owner_id,
        owner_name=owner_name,
        name=name or "Untitled Assistant",
        description="",
        instructions="",
        vector_index_id="assistant_knowledge_base",
        visibility="PRIVATE",
        tags=[],
        starters=[],
        usage_count=0,
        created_at=now,
        updated_at=now,
        status="DRAFT",
    )
    db = await _get_db()
    await db["assistants"].insert_one(_to_doc(assistant))
    logger.info("Created draft assistant %s for owner %s", assistant.assistant_id, owner_id)
    return assistant


async def create_assistant(
    owner_id: str,
    owner_name: str,
    name: str,
    description: str,
    instructions: str,
    vector_index_id: Optional[str] = None,
    visibility: str = "PRIVATE",
    tags: Optional[List[str]] = None,
    starters: Optional[List[str]] = None,
    emoji: Optional[str] = None,
) -> Assistant:
    now = _now()
    assistant = Assistant(
        assistant_id=_generate_assistant_id(),
        owner_id=owner_id,
        owner_name=owner_name,
        name=name,
        description=description,
        instructions=instructions,
        vector_index_id=vector_index_id or "assistant_knowledge_base",
        visibility=visibility,
        tags=tags or [],
        starters=starters or [],
        emoji=emoji,
        usage_count=0,
        created_at=now,
        updated_at=now,
        status="COMPLETE",
    )
    db = await _get_db()
    await db["assistants"].insert_one(_to_doc(assistant))
    logger.info("Created assistant %s for owner %s", assistant.assistant_id, owner_id)
    return assistant


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def get_assistant(assistant_id: str, owner_id: str) -> Optional[Assistant]:
    db = await _get_db()
    doc = await db["assistants"].find_one({"_id": assistant_id, "ownerId": owner_id})
    if not doc:
        return None
    return _from_doc(doc)


async def get_assistant_with_access_check(
    assistant_id: str, user_id: str, user_email: Optional[str] = None
) -> Optional[Assistant]:
    db = await _get_db()
    doc = await db["assistants"].find_one({"_id": assistant_id})
    if not doc:
        return None

    assistant = _from_doc(doc)

    if assistant.visibility == "PRIVATE":
        if assistant.owner_id != user_id:
            logger.warning("Access denied: %s tried to access PRIVATE assistant %s", user_id, assistant_id)
            return None
    elif assistant.visibility == "SHARED":
        if assistant.owner_id != user_id:
            if not user_email:
                logger.warning("user_email required for SHARED assistant %s", assistant_id)
                return None
            if not await check_share_access(assistant_id, user_email):
                logger.warning("Access denied: %s (%s) has no share record for %s", user_id, user_email, assistant_id)
                return None

    return assistant


async def assistant_exists(assistant_id: str) -> bool:
    db = await _get_db()
    return await db["assistants"].count_documents({"_id": assistant_id}, limit=1) > 0


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

async def update_assistant(
    assistant_id: str,
    owner_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    instructions: Optional[str] = None,
    visibility: Optional[str] = None,
    tags: Optional[List[str]] = None,
    starters: Optional[List[str]] = None,
    emoji: Optional[str] = None,
    status: Optional[str] = None,
    image_url: Optional[str] = None,
) -> Optional[Assistant]:
    existing = await get_assistant(assistant_id, owner_id)
    if not existing:
        return None

    updates: dict = {"updatedAt": _now()}
    if name is not None:
        updates["name"] = name
    if description is not None:
        updates["description"] = description
    if instructions is not None:
        updates["instructions"] = instructions
    if visibility is not None:
        updates["visibility"] = visibility
    if tags is not None:
        updates["tags"] = tags
    if starters is not None:
        updates["starters"] = starters
    if emoji is not None:
        updates["emoji"] = emoji
    if status is not None:
        updates["status"] = status
    if image_url is not None:
        updates["imageUrl"] = image_url

    from pymongo import ReturnDocument
    db = await _get_db()
    result = await db["assistants"].find_one_and_update(
        {"_id": assistant_id, "ownerId": owner_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        return None
    logger.info("Updated assistant %s", assistant_id)
    return _from_doc(result)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

async def list_user_assistants(
    owner_id: str,
    limit: Optional[int] = None,
    next_token: Optional[str] = None,
    include_drafts: bool = False,
    include_public: bool = False,  # kept for signature compat; ignored
) -> Tuple[List[Assistant], Optional[str]]:
    db = await _get_db()

    query: dict = {"ownerId": owner_id}
    if not include_drafts:
        query["status"] = {"$ne": "DRAFT"}

    # Cursor-based pagination: next_token is base64(created_at of last returned item)
    if next_token:
        try:
            cursor_ts = base64.b64decode(next_token).decode("utf-8")
            query["createdAt"] = {"$lt": cursor_ts}
        except Exception:
            logger.warning("Invalid next_token, ignoring pagination")

    cursor = db["assistants"].find(query).sort("createdAt", -1)
    if limit and limit > 0:
        cursor = cursor.limit(limit + 1)

    docs = await cursor.to_list(length=None)

    new_token: Optional[str] = None
    if limit and limit > 0 and len(docs) > limit:
        docs = docs[:limit]
        new_token = base64.b64encode(docs[-1]["createdAt"].encode()).decode()

    assistants = [_from_doc(d) for d in docs]
    logger.info("Listed %d assistants for owner %s", len(assistants), owner_id)
    return assistants, new_token


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

async def delete_assistant(assistant_id: str, owner_id: str) -> bool:
    existing = await get_assistant(assistant_id, owner_id)
    if not existing:
        return False

    db = await _get_db()
    result = await db["assistants"].delete_one({"_id": assistant_id, "ownerId": owner_id})
    if result.deleted_count:
        # Clean up share records
        await db["assistant_shares"].delete_many({"assistantId": assistant_id})
        logger.info("Deleted assistant %s", assistant_id)
        return True
    return False


# ---------------------------------------------------------------------------
# Share management
# ---------------------------------------------------------------------------

async def share_assistant(assistant_id: str, owner_id: str, emails: List[str]) -> bool:
    if not await get_assistant(assistant_id, owner_id):
        logger.warning("Cannot share %s: not found or not owned by %s", assistant_id, owner_id)
        return False

    normalized = [e.lower().strip() for e in emails if e.strip()]
    if not normalized:
        return False

    db = await _get_db()
    now = _now()
    for email in normalized:
        await db["assistant_shares"].update_one(
            {"assistantId": assistant_id, "email": email},
            {"$setOnInsert": {"assistantId": assistant_id, "email": email, "firstInteracted": False, "createdAt": now}},
            upsert=True,
        )
    logger.info("Shared assistant %s with %d emails", assistant_id, len(normalized))
    return True


async def unshare_assistant(assistant_id: str, owner_id: str, emails: List[str]) -> bool:
    if not await get_assistant(assistant_id, owner_id):
        logger.warning("Cannot unshare %s: not found or not owned by %s", assistant_id, owner_id)
        return False

    normalized = [e.lower().strip() for e in emails if e.strip()]
    if not normalized:
        return False

    db = await _get_db()
    await db["assistant_shares"].delete_many({"assistantId": assistant_id, "email": {"$in": normalized}})
    logger.info("Unshared assistant %s from %d emails", assistant_id, len(normalized))
    return True


async def list_assistant_shares(assistant_id: str, owner_id: str) -> List[str]:
    if not await get_assistant(assistant_id, owner_id):
        return []

    db = await _get_db()
    docs = await db["assistant_shares"].find({"assistantId": assistant_id}, {"email": 1}).to_list(length=None)
    return [d["email"] for d in docs if "email" in d]


async def check_share_access(assistant_id: str, user_email: str) -> bool:
    db = await _get_db()
    normalized = user_email.lower().strip()
    return await db["assistant_shares"].count_documents(
        {"assistantId": assistant_id, "email": normalized}, limit=1
    ) > 0


async def mark_share_as_interacted(assistant_id: str, user_email: str) -> bool:
    db = await _get_db()
    normalized = user_email.lower().strip()
    result = await db["assistant_shares"].update_one(
        {"assistantId": assistant_id, "email": normalized},
        {"$set": {"firstInteracted": True}},
    )
    return result.matched_count > 0


async def list_shared_with_user(user_email: str) -> List[Assistant]:
    db = await _get_db()
    normalized = user_email.lower().strip()

    share_docs = await db["assistant_shares"].find({"email": normalized}).to_list(length=None)
    if not share_docs:
        return []

    assistant_ids = [d["assistantId"] for d in share_docs]
    first_interacted_map = {d["assistantId"]: d.get("firstInteracted", False) for d in share_docs}

    asst_docs = await db["assistants"].find({"_id": {"$in": assistant_ids}}).to_list(length=None)

    result = []
    for doc in asst_docs:
        assistant = _from_doc(doc)
        assistant.first_interacted = first_interacted_map.get(assistant.assistant_id, False)
        result.append(assistant)

    logger.info("Found %d assistants shared with %s", len(result), normalized)
    return result
