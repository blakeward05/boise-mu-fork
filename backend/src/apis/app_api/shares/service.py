"""Share service layer — MongoDB implementation.

Business logic for creating, retrieving, updating, and revoking
conversation share snapshots. Supports multiple shares per session.
"""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from apis.shared.auth.models import User
from apis.shared.database import get_database, Collections
from apis.shared.sessions.messages import get_messages
from apis.shared.sessions.metadata import get_session_metadata, store_session_metadata

from .models import (
    CreateShareRequest,
    ShareListResponse,
    ShareResponse,
    SharedConversationResponse,
    UpdateShareRequest,
)

logger = logging.getLogger(__name__)


class ShareService:
    """CRUD operations on the shared_conversations MongoDB collection."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_share(
        self,
        session_id: str,
        user: User,
        request: CreateShareRequest,
    ) -> ShareResponse:
        metadata = await get_session_metadata(session_id=session_id, user_id=user.user_id)
        if not metadata:
            raise SessionNotFoundError(session_id)

        messages_response = await get_messages(session_id=session_id, user_id=user.user_id)
        messages_snapshot = [
            msg.model_dump(by_alias=True, exclude_none=True)
            for msg in messages_response.messages
        ]
        metadata_snapshot = metadata.model_dump(by_alias=True, exclude_none=True)

        share_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        allowed_emails = self._resolve_allowed_emails(
            request.access_level, request.allowed_emails, user.email
        )

        doc: dict = {
            "_id": share_id,
            "share_id": share_id,
            "session_id": session_id,
            "owner_id": user.user_id,
            "owner_email": user.email,
            "access_level": request.access_level,
            "created_at": now,
            "metadata": metadata_snapshot,
            "messages": messages_snapshot,
        }
        if allowed_emails is not None:
            doc["allowed_emails"] = allowed_emails

        db = get_database()
        await db[Collections.SHARED_CONVERSATIONS].insert_one(doc)
        logger.info(f"Created share {self._sanitize_id(share_id)} for session {self._sanitize_id(session_id)}")
        return self._build_share_response(doc)

    async def get_shared_conversation(
        self,
        share_id: str,
        requester: User,
    ) -> SharedConversationResponse:
        item = await self._get_share_item(share_id)
        if not item:
            raise ShareNotFoundError()
        self._check_access(item, requester)
        return self._build_shared_conversation_response(item)

    async def update_share(
        self,
        share_id: str,
        user: User,
        request: UpdateShareRequest,
    ) -> ShareResponse:
        item = await self._get_share_item(share_id)
        if not item:
            raise ShareNotFoundError()
        if item["owner_id"] != user.user_id:
            raise NotOwnerError()

        delta: dict = {}
        new_access = request.access_level or item.get("access_level")

        if request.access_level is not None:
            delta["access_level"] = request.access_level

        if new_access == "specific":
            emails = request.allowed_emails or item.get("allowed_emails", [])
            delta["allowed_emails"] = self._resolve_allowed_emails(new_access, emails, user.email)
        elif request.access_level is not None:
            delta["allowed_emails"] = None

        if not delta:
            return self._build_share_response(item)

        db = get_database()
        update: dict = {"$set": {k: v for k, v in delta.items() if v is not None}}
        unset = {k: "" for k, v in delta.items() if v is None}
        if unset:
            update["$unset"] = unset

        await db[Collections.SHARED_CONVERSATIONS].update_one({"_id": share_id}, update)
        updated = await self._get_share_item(share_id)
        return self._build_share_response(updated or item)

    async def revoke_share(self, share_id: str, user: User) -> None:
        item = await self._get_share_item(share_id)
        if not item:
            raise ShareNotFoundError()
        if item["owner_id"] != user.user_id:
            raise NotOwnerError()
        db = get_database()
        await db[Collections.SHARED_CONVERSATIONS].delete_one({"_id": share_id})
        logger.info(f"Revoked share {share_id}")

    async def delete_shares_for_session(self, session_id: str) -> int:
        try:
            db = get_database()
            result = await db[Collections.SHARED_CONVERSATIONS].delete_many({"session_id": session_id})
            count = result.deleted_count
            if count > 0:
                logger.info(f"Deleted {count} share(s) for session {self._sanitize_id(session_id)}")
            return count
        except Exception:
            logger.error(f"Failed to delete shares for session {self._sanitize_id(session_id)}", exc_info=True)
            return 0

    async def get_shares_for_session(self, session_id: str, user_id: str) -> ShareListResponse:
        db = get_database()
        cursor = db[Collections.SHARED_CONVERSATIONS].find(
            {"session_id": session_id, "owner_id": user_id}
        )
        shares = [self._build_share_response(doc) async for doc in cursor]
        return ShareListResponse(shares=shares)

    async def export_shared_conversation(
        self,
        share_id: str,
        requester: User,
    ) -> dict:
        item = await self._get_share_item(share_id)
        if not item:
            raise ShareNotFoundError()
        self._check_access(item, requester)

        snapshot_messages = item.get("messages", [])
        metadata = item.get("metadata", {})
        original_title = metadata.get("title", "Untitled Conversation")
        new_title = f"{original_title} (shared)"

        new_session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        message_count = await self._copy_messages_to_session(
            new_session_id, requester.user_id, snapshot_messages
        )

        from apis.shared.sessions.models import SessionMetadata

        session_meta = SessionMetadata(
            session_id=new_session_id,
            user_id=requester.user_id,
            title=new_title,
            status="active",
            created_at=now,
            last_message_at=now,
            message_count=message_count,
        )
        await store_session_metadata(
            session_id=new_session_id,
            user_id=requester.user_id,
            session_metadata=session_meta,
        )

        logger.info(f"Exported share {self._sanitize_id(share_id)} to session {self._sanitize_id(new_session_id)}")
        return {"sessionId": new_session_id, "title": new_title}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _copy_messages_to_session(
        self,
        session_id: str,
        user_id: str,
        snapshot_messages: list,
    ) -> int:
        """
        Write snapshot messages into a new session via AgentCore Memory SDK
        if available. Phase 3 will replace this with MongoSessionManager.
        """
        if not snapshot_messages:
            return 0
        import asyncio
        try:
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
            from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
            from strands.types.session import SessionMessage
            import os

            memory_id = os.environ.get("AGENTCORE_MEMORY_ID")
            aws_region = os.environ.get("AWS_REGION", "us-west-2")
            if not memory_id:
                logger.debug("AGENTCORE_MEMORY_ID not set — skipping message copy")
                return 0

            config = AgentCoreMemoryConfig(
                memory_id=memory_id, session_id=session_id, actor_id=user_id, enable_prompt_caching=False
            )
            mgr = AgentCoreMemorySessionManager(agentcore_memory_config=config, region_name=aws_region)

            count = 0
            for idx, msg_dict in enumerate(snapshot_messages):
                converse_msg = self._snapshot_msg_to_converse(msg_dict)
                if converse_msg is None:
                    continue
                try:
                    session_msg = SessionMessage.from_message(converse_msg, index=idx)
                    await asyncio.to_thread(mgr.create_message, session_id, "default", session_msg)
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to copy message {idx}: {e}")
            return count
        except ImportError:
            logger.debug("AgentCore Memory SDK not available — export creates empty session (Phase 3 will fix)")
            return 0

    @staticmethod
    def _snapshot_msg_to_converse(msg: dict) -> Optional[dict]:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            return None
        raw_content = msg.get("content", [])
        converse_content = []
        for block in raw_content:
            block_type = block.get("type") if isinstance(block, dict) else None
            if block_type == "text" and block.get("text"):
                converse_content.append({"text": block["text"]})
            elif block_type == "toolUse" and block.get("toolUse"):
                converse_content.append({"toolUse": block["toolUse"]})
            elif block_type == "toolResult" and block.get("toolResult"):
                converse_content.append({"toolResult": block["toolResult"]})
            elif block_type == "image" and block.get("image"):
                converse_content.append({"image": block["image"]})
            elif block_type == "document" and block.get("document"):
                converse_content.append({"document": block["document"]})
        if not converse_content:
            return None
        return {"role": role, "content": converse_content}

    @staticmethod
    def _sanitize_id(value: str, max_length: int = 128) -> str:
        return re.sub(r"[^a-zA-Z0-9\-_]", "", value)[:max_length]

    async def _get_share_item(self, share_id: str) -> Optional[dict]:
        db = get_database()
        return await db[Collections.SHARED_CONVERSATIONS].find_one({"_id": share_id})

    @staticmethod
    def _resolve_allowed_emails(
        access_level: str,
        allowed_emails: Optional[List[str]],
        owner_email: str,
    ) -> Optional[List[str]]:
        if access_level != "specific":
            return None
        emails = list(allowed_emails or [])
        if owner_email.lower() not in [e.lower() for e in emails]:
            emails.insert(0, owner_email)
        return emails

    def _check_access(self, item: dict, requester: User) -> None:
        access_level = item.get("access_level", "specific")
        if requester.user_id == item["owner_id"]:
            return
        if access_level == "public":
            return
        if access_level == "specific":
            allowed = [e.lower() for e in item.get("allowed_emails", [])]
            if requester.email.lower() in allowed:
                return
        raise AccessDeniedError()

    def _build_share_response(self, item: dict) -> ShareResponse:
        return ShareResponse(
            share_id=item["share_id"],
            session_id=item["session_id"],
            owner_id=item["owner_id"],
            access_level=item["access_level"],
            allowed_emails=item.get("allowed_emails"),
            created_at=item["created_at"],
            share_url=f"/shared/{item['share_id']}",
        )

    def _build_shared_conversation_response(self, item: dict) -> SharedConversationResponse:
        from apis.shared.sessions.models import MessageResponse

        metadata = item.get("metadata", {})
        raw_messages = item.get("messages", [])
        messages = []
        for msg_data in raw_messages:
            try:
                messages.append(MessageResponse.model_validate(msg_data))
            except Exception as e:
                logger.warning(f"Skipping malformed message in share {item['share_id']}: {e}")

        return SharedConversationResponse(
            share_id=item["share_id"],
            title=metadata.get("title", "Untitled Conversation"),
            access_level=item["access_level"],
            created_at=item["created_at"],
            owner_id=item["owner_id"],
            messages=messages,
        )


# ------------------------------------------------------------------
# Domain exceptions
# ------------------------------------------------------------------

class SessionNotFoundError(Exception):
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class ShareNotFoundError(Exception):
    pass


class NotOwnerError(Exception):
    pass


class AccessDeniedError(Exception):
    pass


# Backward-compat alias — routes may still import this name
ShareTableNotFoundError = ShareNotFoundError


# Global service instance (singleton)
_service_instance: Optional[ShareService] = None


def get_share_service() -> ShareService:
    global _service_instance
    if _service_instance is None:
        _service_instance = ShareService()
    return _service_instance
