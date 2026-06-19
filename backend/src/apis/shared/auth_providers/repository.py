"""MongoDB repository for OIDC auth provider management."""

import logging
import os
from typing import Optional, List
from datetime import datetime, timezone

from apis.shared.database import get_database, BaseRepository, Collections
from .models import AuthProvider, AuthProviderCreate, AuthProviderUpdate

logger = logging.getLogger(__name__)

_repository: Optional["AuthProviderRepository"] = None


def get_auth_provider_repository() -> "AuthProviderRepository":
    global _repository
    if _repository is None:
        _repository = AuthProviderRepository()
    return _repository


class AuthProviderRepository(BaseRepository):
    """MongoDB repository for OIDC provider configurations.

    Document schema (_id = provider_id):
        _id:               provider_id
        display_name:      human-readable name
        provider_type:     e.g. "oidc"
        enabled:           bool
        issuer_url:        OIDC issuer
        client_id:         app client ID
        ...all other AuthProvider fields...
        # client_secret stored as env var LOCAL_OIDC_SECRET_{PROVIDER_ID}
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.AUTH_PROVIDERS)

    async def get_provider(self, provider_id: str) -> Optional[AuthProvider]:
        doc = await self._find_one({"_id": provider_id})
        return AuthProvider(**self._doc_to_model(doc)) if doc else None

    async def list_providers(self, enabled_only: bool = False) -> List[AuthProvider]:
        filt = {"enabled": True} if enabled_only else {}
        docs = await self._find_many(filt)
        return [AuthProvider(**self._doc_to_model(d)) for d in docs]

    async def create_provider(
        self,
        data: AuthProviderCreate,
        created_by: Optional[str] = None,
        cognito_provider_name: Optional[str] = None,
    ) -> AuthProvider:
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "_id": data.provider_id,
            **data.model_dump(by_alias=False, exclude_none=False),
            "cognito_provider_name": cognito_provider_name,
            "agentcore_runtime_arn": None,
            "agentcore_runtime_id": None,
            "agentcore_runtime_endpoint_url": None,
            "agentcore_runtime_status": "not_configured",
            "agentcore_runtime_error": None,
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
        }
        await self._insert_one(doc)
        logger.info("Created auth provider: %s", data.provider_id)
        return AuthProvider(**self._doc_to_model(doc))

    async def update_provider(
        self, provider_id: str, updates: AuthProviderUpdate
    ) -> Optional[AuthProvider]:
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
        """Read client secret from env var LOCAL_OIDC_SECRET_{PROVIDER_ID}."""
        key = f"LOCAL_OIDC_SECRET_{provider_id.upper().replace('-', '_')}"
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
