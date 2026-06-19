"""MongoDB repository for API key management."""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from apis.shared.database import get_database, BaseRepository, Collections

logger = logging.getLogger(__name__)

_repository: Optional["ApiKeyRepository"] = None


def get_api_key_repository() -> "ApiKeyRepository":
    global _repository
    if _repository is None:
        _repository = ApiKeyRepository()
    return _repository


class ApiKeyRepository(BaseRepository):
    """MongoDB repository for API keys.

    Document schema (_id = key_id):
        _id:          key_id
        user_id:      owner user ID
        name:         human label
        key_hash:     SHA-256 hex of the raw key (for lookup)
        key_prefix:   first 8 chars of raw key (displayed to user)
        created_at:   ISO timestamp
        expires_at:   ISO timestamp or None
        last_used_at: ISO timestamp or None
        enabled:      bool
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.API_KEYS)

    async def create_key(self, item: Dict[str, Any]) -> None:
        doc = {
            "_id": item["keyId"],
            "user_id": item["userId"],
            "name": item.get("name", ""),
            "key_hash": item["keyHash"],
            "key_prefix": item.get("keyPrefix", ""),
            "created_at": item.get("createdAt", datetime.now(timezone.utc).isoformat()),
            "expires_at": item.get("expiresAt"),
            "last_used_at": item.get("lastUsedAt"),
            "enabled": True,
        }
        await self._insert_one(doc)
        logger.info("Created API key %s for user %s", item["keyId"], item["userId"])

    async def get_key(self, user_id: str, key_id: str) -> Optional[Dict[str, Any]]:
        doc = await self._find_one({"_id": key_id, "user_id": user_id})
        return self._doc_to_item(doc) if doc else None

    async def get_key_for_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        doc = await self._find_one({"user_id": user_id, "enabled": True})
        return self._doc_to_item(doc) if doc else None

    async def get_key_by_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        doc = await self._find_one({"key_hash": key_hash, "enabled": True})
        return self._doc_to_item(doc) if doc else None

    async def delete_key(self, user_id: str, key_id: str) -> bool:
        count = await self._delete_one({"_id": key_id, "user_id": user_id})
        return count > 0

    async def update_last_used(self, user_id: str, key_id: str) -> None:
        await self._update_one(
            {"_id": key_id, "user_id": user_id},
            {"$set": {"last_used_at": datetime.now(timezone.utc).isoformat()}},
        )

    @staticmethod
    def _doc_to_item(doc: dict) -> Dict[str, Any]:
        return {
            "keyId": doc["_id"],
            "userId": doc["user_id"],
            "name": doc.get("name", ""),
            "keyHash": doc.get("key_hash", ""),
            "keyPrefix": doc.get("key_prefix", ""),
            "createdAt": doc.get("created_at", ""),
            "expiresAt": doc.get("expires_at"),
            "lastUsedAt": doc.get("last_used_at"),
        }
