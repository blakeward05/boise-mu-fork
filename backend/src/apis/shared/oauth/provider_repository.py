"""MongoDB repository for OAuth provider configurations."""

import logging
import os
from typing import Optional, List
from datetime import datetime, timezone

from apis.shared.database import get_database, BaseRepository, Collections
from .models import OAuthProvider, OAuthProviderCreate, OAuthProviderUpdate

logger = logging.getLogger(__name__)

_repository: Optional["OAuthProviderRepository"] = None


def get_provider_repository() -> "OAuthProviderRepository":
    global _repository
    if _repository is None:
        _repository = OAuthProviderRepository()
    return _repository


class OAuthProviderRepository(BaseRepository):
    """MongoDB repository for OAuth provider configurations (Google, Azure, GitHub, Canvas…).

    Document schema (_id = provider_id):
        _id:                    provider_id
        display_name:           human label
        provider_type:          OAuthProviderType value
        authorization_endpoint: URL
        token_endpoint:         URL
        client_id:              app client ID
        scopes:                 list of scope strings
        allowed_roles:          list of role strings
        enabled:                bool
        icon_name:              icon identifier
        userinfo_endpoint:      optional URL
        revocation_endpoint:    optional URL
        pkce_required:          bool
        authorization_params:   dict
        created_at:             ISO timestamp
        updated_at:             ISO timestamp
        # client_secret stored as env var OAUTH_SECRET_{PROVIDER_ID}
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.OAUTH_PROVIDERS)

    async def get_provider(self, provider_id: str) -> Optional[OAuthProvider]:
        doc = await self._find_one({"_id": provider_id})
        return OAuthProvider(**self._doc_to_model(doc)) if doc else None

    async def list_providers(self, enabled_only: bool = False) -> List[OAuthProvider]:
        filt = {"enabled": True} if enabled_only else {}
        docs = await self._find_many(filt)
        return [OAuthProvider(**self._doc_to_model(d)) for d in docs]

    async def create_provider(self, create_request: OAuthProviderCreate) -> OAuthProvider:
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "_id": create_request.provider_id,
            **create_request.model_dump(by_alias=False, exclude_none=False),
            "created_at": now,
            "updated_at": now,
        }
        await self._insert_one(doc)
        logger.info("Created OAuth provider: %s", create_request.provider_id)
        return OAuthProvider(**self._doc_to_model(doc))

    async def update_provider(
        self, provider_id: str, updates: OAuthProviderUpdate
    ) -> Optional[OAuthProvider]:
        now = datetime.now(timezone.utc).isoformat()
        delta = {
            k: v
            for k, v in updates.model_dump(by_alias=False, exclude_none=True).items()
        }
        delta["updated_at"] = now
        count = await self._update_one({"_id": provider_id}, {"$set": delta})
        if count == 0:
            return None
        return await self.get_provider(provider_id)

    async def delete_provider(self, provider_id: str) -> bool:
        await self._delete_client_secret(provider_id)
        count = await self._delete_one({"_id": provider_id})
        return count > 0

    async def get_client_secret(self, provider_id: str) -> Optional[str]:
        key = f"OAUTH_SECRET_{provider_id.upper().replace('-', '_')}"
        secret = os.environ.get(key)
        if not secret:
            doc = await self._find_one({"_id": provider_id}, {"_client_secret": 1})
            secret = doc.get("_client_secret") if doc else None
        return secret

    async def _store_client_secret(self, provider_id: str, client_secret: str) -> None:
        await self._update_one(
            {"_id": provider_id},
            {"$set": {"_client_secret": client_secret}},
        )

    async def _delete_client_secret(self, provider_id: str) -> None:
        await self._update_one(
            {"_id": provider_id},
            {"$unset": {"_client_secret": ""}},
        )

    @staticmethod
    def _doc_to_model(doc: dict) -> dict:
        d = dict(doc)
        d["provider_id"] = d.pop("_id", d.get("provider_id"))
        d.pop("_client_secret", None)
        return d
