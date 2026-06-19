"""MongoDB repository for system settings (first-boot state)."""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from apis.shared.database import get_database, BaseRepository, Collections

logger = logging.getLogger(__name__)

_FIRST_BOOT_ID = "first-boot"

_repository: Optional["SystemSettingsRepository"] = None


def get_system_settings_repository() -> "SystemSettingsRepository":
    global _repository
    if _repository is None:
        _repository = SystemSettingsRepository()
    return _repository


class SystemSettingsRepository(BaseRepository):
    """MongoDB repository for system-level settings.

    Uses the USER_SETTINGS collection with a reserved _id prefix "system:".

    First-boot document (_id = "system:first-boot"):
        completed:    bool
        completed_at: ISO timestamp
        completed_by: user_id
        admin_username: str
        admin_email:    str
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.USER_SETTINGS)

    async def get_first_boot_status(self) -> Optional[Dict[str, Any]]:
        doc = await self._find_one({"_id": f"system:{_FIRST_BOOT_ID}"})
        if not doc:
            return None
        return {
            "completed": doc.get("completed", False),
            "completedAt": doc.get("completed_at"),
            "completedBy": doc.get("completed_by"),
            "adminUsername": doc.get("admin_username"),
            "adminEmail": doc.get("admin_email"),
        }

    async def mark_first_boot_completed(
        self, user_id: str, username: str, email: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._upsert(
            {"_id": f"system:{_FIRST_BOOT_ID}"},
            {
                "_id": f"system:{_FIRST_BOOT_ID}",
                "completed": True,
                "completed_at": now,
                "completed_by": user_id,
                "admin_username": username,
                "admin_email": email,
            },
        )
        logger.info("First-boot completed by %s (%s)", username, email)
