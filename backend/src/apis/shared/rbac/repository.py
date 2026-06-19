"""MongoDB repository for RBAC AppRole management."""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from apis.shared.database import get_database, BaseRepository, Collections
from .models import AppRole, EffectivePermissions

logger = logging.getLogger(__name__)

_repository: Optional["AppRoleRepository"] = None


def get_app_role_repository() -> "AppRoleRepository":
    global _repository
    if _repository is None:
        _repository = AppRoleRepository()
    return _repository


class AppRoleRepository(BaseRepository):
    """MongoDB repository for AppRole definitions.

    Document schema (_id = role_id):
        _id:                   role_id
        display_name:          human label
        description:           text description
        jwt_role_mappings:     list of JWT role strings that map to this role
        inherits_from:         list of role_ids this role inherits
        effective_permissions: EffectivePermissions dict
        granted_tools:         list of tool_ids
        granted_models:        list of model_ids
        priority:              int (lower = higher priority)
        is_system_role:        bool
        enabled:               bool
        created_at:            ISO timestamp
        updated_at:            ISO timestamp
        created_by:            user_id or None
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.APP_ROLES)

    async def get_role(self, role_id: str) -> Optional[AppRole]:
        doc = await self._find_one({"_id": role_id})
        return self._doc_to_role(doc) if doc else None

    async def role_exists(self, role_id: str) -> bool:
        return await self._count({"_id": role_id}) > 0

    async def list_roles(self, enabled_only: bool = False) -> List[AppRole]:
        filt = {"enabled": True} if enabled_only else {}
        docs = await self._find_many(filt, sort=[("priority", 1)])
        return [self._doc_to_role(d) for d in docs]

    async def create_role(self, role: AppRole) -> AppRole:
        doc = self._role_to_doc(role)
        await self._insert_one(doc)
        logger.info("Created AppRole: %s", role.role_id)
        return role

    async def update_role(self, role: AppRole) -> AppRole:
        doc = self._role_to_doc(role)
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._upsert({"_id": role.role_id}, doc)
        return role

    async def delete_role(self, role_id: str) -> bool:
        count = await self._delete_one({"_id": role_id})
        return count > 0

    # ── Mapping queries ────────────────────────────────────────────

    async def get_roles_for_jwt_role(self, jwt_role: str) -> List[str]:
        """Return role_ids that list jwt_role in their jwt_role_mappings."""
        docs = await self._find_many(
            {"jwt_role_mappings": jwt_role, "enabled": True},
            projection={"_id": 1},
        )
        return [d["_id"] for d in docs]

    async def get_roles_for_tool(self, tool_id: str) -> List[Dict[str, Any]]:
        docs = await self._find_many(
            {"granted_tools": tool_id, "enabled": True},
            projection={"_id": 1, "display_name": 1},
        )
        return [{"role_id": d["_id"], "display_name": d.get("display_name")} for d in docs]

    async def get_roles_for_model(self, model_id: str) -> List[Dict[str, Any]]:
        docs = await self._find_many(
            {"granted_models": model_id, "enabled": True},
            projection={"_id": 1, "display_name": 1},
        )
        return [{"role_id": d["_id"], "display_name": d.get("display_name")} for d in docs]

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _role_to_doc(role: AppRole) -> dict:
        perms = (
            role.effective_permissions.model_dump()
            if hasattr(role.effective_permissions, "model_dump")
            else (role.effective_permissions or {})
        )
        return {
            "_id": role.role_id,
            "display_name": role.display_name,
            "description": role.description,
            "jwt_role_mappings": role.jwt_role_mappings or [],
            "inherits_from": role.inherits_from or [],
            "effective_permissions": perms,
            "granted_tools": role.granted_tools or [],
            "granted_models": role.granted_models or [],
            "priority": role.priority,
            "is_system_role": role.is_system_role,
            "enabled": role.enabled,
            "created_at": role.created_at,
            "updated_at": role.updated_at,
            "created_by": role.created_by,
        }

    @staticmethod
    def _doc_to_role(doc: dict) -> AppRole:
        perms_raw = doc.get("effective_permissions") or {}
        perms = (
            EffectivePermissions(**perms_raw)
            if isinstance(perms_raw, dict)
            else perms_raw
        )
        return AppRole(
            role_id=doc["_id"],
            display_name=doc.get("display_name", ""),
            description=doc.get("description", ""),
            jwt_role_mappings=doc.get("jwt_role_mappings", []),
            inherits_from=doc.get("inherits_from", []),
            effective_permissions=perms,
            granted_tools=doc.get("granted_tools", []),
            granted_models=doc.get("granted_models", []),
            priority=doc.get("priority", 100),
            is_system_role=doc.get("is_system_role", False),
            enabled=doc.get("enabled", True),
            created_at=doc.get("created_at", ""),
            updated_at=doc.get("updated_at", ""),
            created_by=doc.get("created_by"),
        )
