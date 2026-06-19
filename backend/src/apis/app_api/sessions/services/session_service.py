"""Session CRUD service — MongoDB implementation."""

import logging
from typing import Optional
from datetime import datetime, timezone

from apis.shared.database import get_database, Collections
from apis.shared.sessions.models import SessionMetadata
from apis.app_api.files.service import get_file_upload_service

logger = logging.getLogger(__name__)


class SessionService:
    """CRUD operations on the sessions MongoDB collection."""

    async def get_session(self, user_id: str, session_id: str) -> Optional[SessionMetadata]:
        try:
            db = get_database()
            doc = await db[Collections.SESSIONS].find_one(
                {"_id": session_id, "user_id": user_id}
            )
            if not doc:
                return None
            doc["session_id"] = str(doc.pop("_id"))
            return SessionMetadata.model_validate(doc)
        except Exception:
            logger.error("Failed to get session", exc_info=True)
            return None

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        try:
            db = get_database()
            now = datetime.now(timezone.utc).isoformat()
            result = await db[Collections.SESSIONS].update_one(
                {"_id": session_id, "user_id": user_id, "status": {"$ne": "deleted"}},
                {"$set": {"status": "deleted", "deleted": True, "deleted_at": now}},
            )
            if result.matched_count == 0:
                logger.info("Session not found or already deleted")
                return False
            logger.info("Soft-deleted session")
            return True
        except Exception:
            logger.error("Failed to delete session", exc_info=True)
            return False

    def delete_agentcore_memory(self, session_id: str, user_id: str) -> None:
        """No-op — AgentCore Memory replaced by MongoSessionManager (Phase 3)."""
        logger.debug("AgentCore Memory cleanup skipped (local mode)")

    def delete_session_files(self, session_id: str) -> None:
        """Delete all files associated with a session (sync, for background tasks)."""
        import asyncio

        try:
            file_service = get_file_upload_service()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                deleted_count = loop.run_until_complete(
                    file_service.delete_session_files(session_id)
                )
                if deleted_count > 0:
                    logger.info("Background task deleted files for session")
            finally:
                loop.close()
        except Exception:
            logger.error("Failed to delete files for session")
