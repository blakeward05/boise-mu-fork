"""MongoDB storage service for managed models.

Replaces the DynamoDB-backed module-level functions with class-based
MongoManagedModelsService. Call get_managed_models_service() for the
singleton instance.
"""

import logging
import uuid
from typing import List, Optional
from datetime import datetime, timezone

from apis.shared.database import get_database, Collections
from .models import ManagedModel, ManagedModelCreate, ManagedModelUpdate

logger = logging.getLogger(__name__)


def _resolve_supports_caching(supports_caching: Optional[bool], provider: str) -> bool:
    if supports_caching is not None:
        return supports_caching
    return provider.lower() == "bedrock"


class MongoManagedModelsService:
    """MongoDB-backed CRUD for managed LLM models."""

    def __init__(self) -> None:
        self._col = get_database()[Collections.MANAGED_MODELS]

    # ── CRUD ───────────────────────────────────────────────────────

    async def create_managed_model(self, model_data: ManagedModelCreate) -> ManagedModel:
        existing = await self._col.find_one({"model_id": model_data.model_id})
        if existing:
            raise ValueError(f"Model {model_data.model_id!r} already exists")

        now = datetime.now(timezone.utc).isoformat()
        internal_id = str(uuid.uuid4())
        supports_caching = _resolve_supports_caching(
            model_data.supports_caching, model_data.provider
        )

        if model_data.is_default:
            await self._clear_existing_default(exclude_id=internal_id)

        doc = {
            "_id": internal_id,
            "model_id": model_data.model_id,
            "model_name": model_data.model_name,
            "provider": model_data.provider,
            "provider_name": model_data.provider_name,
            "input_modalities": model_data.input_modalities,
            "output_modalities": model_data.output_modalities,
            "max_input_tokens": model_data.max_input_tokens,
            "max_output_tokens": model_data.max_output_tokens,
            "allowed_app_roles": model_data.allowed_app_roles,
            "available_to_roles": model_data.available_to_roles,
            "enabled": model_data.enabled,
            "input_price_per_million_tokens": model_data.input_price_per_million_tokens,
            "output_price_per_million_tokens": model_data.output_price_per_million_tokens,
            "cache_write_price_per_million_tokens": model_data.cache_write_price_per_million_tokens,
            "cache_read_price_per_million_tokens": model_data.cache_read_price_per_million_tokens,
            "is_reasoning_model": model_data.is_reasoning_model,
            "knowledge_cutoff_date": model_data.knowledge_cutoff_date,
            "supports_caching": supports_caching,
            "is_default": model_data.is_default,
            "endpoint_url": model_data.endpoint_url,
            "api_key_env_var": model_data.api_key_env_var,
            "extra_headers": model_data.extra_headers,
            "databricks_use_invocations": model_data.databricks_use_invocations,
            "databricks_responses_api": model_data.databricks_responses_api,
            "created_at": now,
            "updated_at": now,
        }
        await self._col.insert_one(doc)
        logger.info("Created managed model: %s (%s)", model_data.model_id, model_data.provider)
        return self._doc_to_model(doc)

    async def get_managed_model(self, model_id: str) -> Optional[ManagedModel]:
        doc = await self._col.find_one({"model_id": model_id})
        return self._doc_to_model(doc) if doc else None

    async def get_managed_model_by_internal_id(self, internal_id: str) -> Optional[ManagedModel]:
        doc = await self._col.find_one({"_id": internal_id})
        return self._doc_to_model(doc) if doc else None

    async def list_managed_models(
        self, enabled_only: bool = False, provider: Optional[str] = None
    ) -> List[ManagedModel]:
        filt: dict = {}
        if enabled_only:
            filt["enabled"] = True
        if provider:
            filt["provider"] = provider
        cursor = self._col.find(filt)
        return [self._doc_to_model(d) async for d in cursor]

    async def update_managed_model(
        self, model_id: str, updates: ManagedModelUpdate
    ) -> Optional[ManagedModel]:
        now = datetime.now(timezone.utc).isoformat()
        delta = {
            k: v
            for k, v in updates.model_dump(by_alias=False, exclude_none=True).items()
        }
        if "supports_caching" in delta or "provider" in delta:
            current = await self.get_managed_model(model_id)
            provider = delta.get("provider", current.provider if current else "unknown")
            delta["supports_caching"] = _resolve_supports_caching(
                delta.get("supports_caching"), provider
            )
        if delta.get("is_default"):
            doc = await self._col.find_one({"model_id": model_id}, {"_id": 1})
            if doc:
                await self._clear_existing_default(exclude_id=str(doc["_id"]))
        delta["updated_at"] = now
        result = await self._col.update_one({"model_id": model_id}, {"$set": delta})
        if result.matched_count == 0:
            return None
        return await self.get_managed_model(model_id)

    async def delete_managed_model(self, model_id: str) -> bool:
        result = await self._col.delete_one({"model_id": model_id})
        return result.deleted_count > 0

    async def get_default_model(self) -> Optional[ManagedModel]:
        doc = await self._col.find_one({"is_default": True, "enabled": True})
        return self._doc_to_model(doc) if doc else None

    # ── Helpers ────────────────────────────────────────────────────

    async def _clear_existing_default(self, exclude_id: Optional[str] = None) -> None:
        filt: dict = {"is_default": True}
        if exclude_id:
            filt["_id"] = {"$ne": exclude_id}
        await self._col.update_many(
            filt,
            {"$set": {"is_default": False, "updated_at": datetime.now(timezone.utc).isoformat()}},
        )

    @staticmethod
    def _doc_to_model(doc: dict) -> ManagedModel:
        d = dict(doc)
        d["id"] = str(d.pop("_id", ""))
        return ManagedModel(**d)


# Module-level singleton — matches the existing import pattern
_service: Optional[MongoManagedModelsService] = None


def get_managed_models_service() -> MongoManagedModelsService:
    global _service
    if _service is None:
        _service = MongoManagedModelsService()
    return _service


# Module-level async functions kept for backward-compat with existing call sites
async def create_managed_model(model_data: ManagedModelCreate) -> ManagedModel:
    return await get_managed_models_service().create_managed_model(model_data)


async def get_managed_model(model_id: str) -> Optional[ManagedModel]:
    return await get_managed_models_service().get_managed_model(model_id)


async def get_managed_model_by_internal_id(internal_id: str) -> Optional[ManagedModel]:
    return await get_managed_models_service().get_managed_model_by_internal_id(internal_id)


async def list_managed_models(
    enabled_only: bool = False, provider: Optional[str] = None
) -> List[ManagedModel]:
    return await get_managed_models_service().list_managed_models(enabled_only, provider)


async def update_managed_model(
    model_id: str, updates: ManagedModelUpdate
) -> Optional[ManagedModel]:
    return await get_managed_models_service().update_managed_model(model_id, updates)


async def delete_managed_model(model_id: str) -> bool:
    return await get_managed_models_service().delete_managed_model(model_id)


async def get_default_model() -> Optional[ManagedModel]:
    return await get_managed_models_service().get_default_model()


async def list_all_managed_models() -> List[ManagedModel]:
    return await get_managed_models_service().list_managed_models(enabled_only=False)
