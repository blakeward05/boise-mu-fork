"""MongoDB repository for OAuth user tokens."""

import logging
from typing import Optional, List
from datetime import datetime, timezone

from apis.shared.database import get_database, BaseRepository, Collections
from .models import OAuthUserToken, OAuthConnectionStatus

logger = logging.getLogger(__name__)

_repository: Optional["OAuthTokenRepository"] = None


def get_token_repository() -> "OAuthTokenRepository":
    global _repository
    if _repository is None:
        _repository = OAuthTokenRepository()
    return _repository


class OAuthTokenRepository(BaseRepository):
    """MongoDB repository for stored OAuth user tokens.

    Document schema (_id = f"{user_id}:{provider_id}"):
        _id:                      composite key
        user_id:                  user ID
        provider_id:              provider ID
        access_token_encrypted:   encrypted access token
        refresh_token_encrypted:  encrypted refresh token or None
        token_type:               e.g. "Bearer"
        expires_at:               unix timestamp or None
        scopes_hash:              hash of granted scopes
        status:                   OAuthConnectionStatus
        connected_at:             ISO timestamp
        updated_at:               ISO timestamp
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.OAUTH_USER_TOKENS)

    @staticmethod
    def _key(user_id: str, provider_id: str) -> str:
        return f"{user_id}:{provider_id}"

    async def get_token(self, user_id: str, provider_id: str) -> Optional[OAuthUserToken]:
        doc = await self._find_one({"_id": self._key(user_id, provider_id)})
        return self._doc_to_token(doc) if doc else None

    async def list_user_tokens(self, user_id: str) -> List[OAuthUserToken]:
        docs = await self._find_many({"user_id": user_id})
        return [self._doc_to_token(d) for d in docs]

    async def list_provider_tokens(self, provider_id: str) -> List[OAuthUserToken]:
        docs = await self._find_many({"provider_id": provider_id})
        return [self._doc_to_token(d) for d in docs]

    async def save_token(self, token: OAuthUserToken) -> OAuthUserToken:
        doc = self._token_to_doc(token)
        await self._upsert({"_id": doc["_id"]}, doc)
        return token

    async def update_token_status(
        self, user_id: str, provider_id: str, status: OAuthConnectionStatus
    ) -> Optional[OAuthUserToken]:
        count = await self._update_one(
            {"_id": self._key(user_id, provider_id)},
            {
                "$set": {
                    "status": status.value if hasattr(status, "value") else status,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        if count == 0:
            return None
        return await self.get_token(user_id, provider_id)

    async def delete_token(self, user_id: str, provider_id: str) -> bool:
        count = await self._delete_one({"_id": self._key(user_id, provider_id)})
        return count > 0

    async def delete_user_tokens(self, user_id: str) -> int:
        result = await self._collection.delete_many({"user_id": user_id})
        return result.deleted_count

    async def delete_provider_tokens(self, provider_id: str) -> int:
        result = await self._collection.delete_many({"provider_id": provider_id})
        return result.deleted_count

    @staticmethod
    def _token_to_doc(token: OAuthUserToken) -> dict:
        status_val = (
            token.status.value if hasattr(token.status, "value") else token.status
        )
        return {
            "_id": f"{token.user_id}:{token.provider_id}",
            "user_id": token.user_id,
            "provider_id": token.provider_id,
            "access_token_encrypted": token.access_token_encrypted,
            "refresh_token_encrypted": token.refresh_token_encrypted,
            "token_type": token.token_type,
            "expires_at": token.expires_at,
            "scopes_hash": token.scopes_hash,
            "status": status_val,
            "connected_at": token.connected_at,
            "updated_at": token.updated_at,
        }

    @staticmethod
    def _doc_to_token(doc: dict) -> OAuthUserToken:
        return OAuthUserToken(
            user_id=doc["user_id"],
            provider_id=doc["provider_id"],
            access_token_encrypted=doc.get("access_token_encrypted", ""),
            refresh_token_encrypted=doc.get("refresh_token_encrypted"),
            token_type=doc.get("token_type", "Bearer"),
            expires_at=doc.get("expires_at"),
            scopes_hash=doc.get("scopes_hash", ""),
            status=doc.get("status", "active"),
            connected_at=doc.get("connected_at", ""),
            updated_at=doc.get("updated_at", ""),
        )
