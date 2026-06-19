"""MongoDB repository for per-user settings."""

import logging
from typing import Optional

from apis.shared.database import get_database, BaseRepository, Collections

logger = logging.getLogger(__name__)

_repository: Optional["UserSettingsRepository"] = None


def get_user_settings_repository() -> "UserSettingsRepository":
    global _repository
    if _repository is None:
        _repository = UserSettingsRepository()
    return _repository


class UserSettingsRepository(BaseRepository):
    """MongoDB repository for user preferences.

    Document schema (_id = user_id):
        _id:              user_id
        default_model_id: optional model_id string
        ...any future settings fields...
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.USER_SETTINGS)

    async def get_settings(self, user_id: str) -> dict:
        doc = await self._find_one({"_id": user_id})
        if not doc:
            return {}
        doc.pop("_id", None)
        return doc

    async def update_settings(self, user_id: str, settings: dict) -> dict:
        clean = {k: v for k, v in settings.items() if k != "_id"}
        doc = {"_id": user_id, **clean}
        await self._upsert({"_id": user_id}, doc)
        return clean
