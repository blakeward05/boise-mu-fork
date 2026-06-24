"""Seed default AppRoles on startup if they don't already exist."""

import logging
from datetime import datetime, timezone

from apis.shared.database import get_database, Collections

logger = logging.getLogger(__name__)

_DEFAULT_ROLES = [
    {
        "_id": "system_admin",
        "display_name": "System Admin",
        "description": "Full administrative access to all features.",
        "jwt_role_mappings": ["system_admin", "SuperAdmin", "Admin"],
        "granted_tools": ["*"],
        "granted_models": ["*"],
        "quota_tier": None,
        "enabled": True,
        "is_system_role": True,
        "priority": 100,
    },
    {
        "_id": "user",
        "display_name": "User",
        "description": "Standard user access.",
        "jwt_role_mappings": ["User", "user"],
        "granted_tools": [],
        "granted_models": [],
        "quota_tier": None,
        "enabled": True,
        "is_system_role": False,
        "priority": 0,
    },
]


async def seed_default_roles() -> None:
    db = get_database()

    # Drop stale role_name_unique index if it exists (leftover from old schema)
    try:
        await db[Collections.APP_ROLES].drop_index("role_name_unique")
        logger.info("Dropped stale role_name_unique index from app_roles")
    except Exception:
        pass  # Index doesn't exist — that's fine

    now = datetime.now(timezone.utc).isoformat()
    for role in _DEFAULT_ROLES:
        doc = {**role, "updated_at": now}
        await db[Collections.APP_ROLES].update_one(
            {"_id": role["_id"]},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        logger.info("Upserted AppRole: %s", role["_id"])
