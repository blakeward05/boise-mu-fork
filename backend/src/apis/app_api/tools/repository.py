"""MongoDB repository for tool catalog and user tool preferences."""

import logging
from typing import Optional, List
from datetime import datetime, timezone

from apis.shared.database import get_database, BaseRepository, Collections
from .models import ToolDefinition, UserToolPreference, ToolStatus

logger = logging.getLogger(__name__)

_repository: Optional["ToolCatalogRepository"] = None


def get_tool_catalog_repository() -> "ToolCatalogRepository":
    global _repository
    if _repository is None:
        _repository = ToolCatalogRepository()
    return _repository


class ToolCatalogRepository(BaseRepository):
    """MongoDB repository for MCP / built-in tool catalog.

    Tool document schema (_id = tool_id):
        _id:         tool_id
        display_name: human label
        description:  text
        category:     grouping string
        status:       ToolStatus value
        enabled:      bool
        config:       dict of tool-specific settings
        created_at:   ISO timestamp
        updated_at:   ISO timestamp

    User preference document stored in USER_SETTINGS collection
    (_id = f"tool_pref:{user_id}") as a nested dict.
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.APP_ROLES)
        # Tools live in a dedicated sub-collection keyed by "tool:" prefix
        self._tools = get_database()["tools"]
        self._prefs = get_database()[Collections.USER_SETTINGS]

    async def get_tool(self, tool_id: str) -> Optional[ToolDefinition]:
        doc = await self._tools.find_one({"_id": tool_id})
        return ToolDefinition(**self._doc(doc)) if doc else None

    async def list_tools(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[ToolDefinition]:
        filt: dict = {}
        if status:
            filt["status"] = status
        if category:
            filt["category"] = category
        cursor = self._tools.find(filt)
        return [ToolDefinition(**self._doc(d)) async for d in cursor]

    async def create_tool(self, tool: ToolDefinition) -> ToolDefinition:
        doc = tool.model_dump(by_alias=False)
        doc["_id"] = tool.tool_id
        doc.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        doc.setdefault("updated_at", doc["created_at"])
        await self._tools.insert_one(doc)
        return tool

    async def update_tool(self, tool: ToolDefinition) -> Optional[ToolDefinition]:
        doc = tool.model_dump(by_alias=False)
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = await self._tools.replace_one({"_id": tool.tool_id}, doc)
        if result.matched_count == 0:
            return None
        return tool

    async def delete_tool(self, tool_id: str) -> bool:
        return (await self._tools.delete_one({"_id": tool_id})).deleted_count > 0

    async def get_tool_status(self, tool_id: str) -> Optional[ToolStatus]:
        doc = await self._tools.find_one({"_id": tool_id}, {"status": 1})
        if not doc:
            return None
        return ToolStatus(doc.get("status", "disabled"))

    # ── User preferences ───────────────────────────────────────────

    async def get_user_preferences(self, user_id: str) -> List[UserToolPreference]:
        doc = await self._prefs.find_one({"_id": f"tool_pref:{user_id}"})
        if not doc:
            return []
        return [UserToolPreference(**p) for p in doc.get("preferences", [])]

    async def save_user_preferences(
        self, user_id: str, preferences: List[UserToolPreference]
    ) -> None:
        await self._prefs.update_one(
            {"_id": f"tool_pref:{user_id}"},
            {
                "$set": {
                    "user_id": user_id,
                    "preferences": [p.model_dump(by_alias=False) for p in preferences],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )

    @staticmethod
    def _doc(doc: dict) -> dict:
        d = dict(doc)
        d["tool_id"] = d.pop("_id", d.get("tool_id"))
        return d
