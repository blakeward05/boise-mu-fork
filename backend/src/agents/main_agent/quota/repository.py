"""MongoDB repository for quota management."""

import logging
import uuid
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta

from pymongo import ASCENDING, DESCENDING

from apis.shared.database import get_database, Collections
from .models import QuotaTier, QuotaAssignment, QuotaEvent, QuotaAssignmentType, QuotaOverride

logger = logging.getLogger(__name__)

_EVENT_TTL_DAYS = 90


class QuotaRepository:
    """MongoDB repository for quota tiers, assignments, events, and overrides."""

    def __init__(self) -> None:
        db = get_database()
        self._tiers = db[Collections.QUOTA_TIERS]
        self._assignments = db[Collections.QUOTA_ASSIGNMENTS]
        self._events = db[Collections.QUOTA_EVENTS]

    # ── Tiers ──────────────────────────────────────────────────────

    async def get_tier(self, tier_id: str) -> Optional[QuotaTier]:
        doc = await self._tiers.find_one({"_id": tier_id})
        return QuotaTier(**self._doc(doc)) if doc else None

    async def list_tiers(self, enabled_only: bool = False) -> List[QuotaTier]:
        filt = {"enabled": True} if enabled_only else {}
        cursor = self._tiers.find(filt)
        return [QuotaTier(**self._doc(d)) async for d in cursor]

    async def create_tier(self, tier: QuotaTier) -> QuotaTier:
        doc = tier.model_dump(by_alias=False)
        doc["_id"] = tier.tier_id
        await self._tiers.insert_one(doc)
        logger.info("Created quota tier: %s", tier.tier_id)
        return tier

    async def update_tier(self, tier_id: str, updates: dict) -> Optional[QuotaTier]:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        count = (await self._tiers.update_one({"_id": tier_id}, {"$set": updates})).modified_count
        if count == 0:
            return None
        return await self.get_tier(tier_id)

    async def delete_tier(self, tier_id: str) -> bool:
        return (await self._tiers.delete_one({"_id": tier_id})).deleted_count > 0

    # ── Assignments ────────────────────────────────────────────────

    async def get_assignment(self, assignment_id: str) -> Optional[QuotaAssignment]:
        doc = await self._assignments.find_one({"_id": assignment_id})
        return QuotaAssignment(**self._doc(doc)) if doc else None

    async def query_user_assignment(self, user_id: str) -> Optional[QuotaAssignment]:
        doc = await self._assignments.find_one(
            {"user_id": user_id, "enabled": True},
            sort=[("priority", ASCENDING)],
        )
        return QuotaAssignment(**self._doc(doc)) if doc else None

    async def query_app_role_assignments(self, app_role_id: str) -> List[QuotaAssignment]:
        cursor = self._assignments.find(
            {"app_role_id": app_role_id, "enabled": True},
            sort=[("priority", ASCENDING)],
        )
        return [QuotaAssignment(**self._doc(d)) async for d in cursor]

    async def query_role_assignments(self, role: str) -> List[QuotaAssignment]:
        cursor = self._assignments.find(
            {"jwt_role": role, "enabled": True},
            sort=[("priority", ASCENDING)],
        )
        return [QuotaAssignment(**self._doc(d)) async for d in cursor]

    async def list_assignments_by_type(
        self, assignment_type: str, enabled_only: bool = False
    ) -> List[QuotaAssignment]:
        filt: Dict[str, Any] = {"assignment_type": assignment_type}
        if enabled_only:
            filt["enabled"] = True
        cursor = self._assignments.find(filt, sort=[("priority", ASCENDING)])
        return [QuotaAssignment(**self._doc(d)) async for d in cursor]

    async def list_all_assignments(self, enabled_only: bool = False) -> List[QuotaAssignment]:
        filt = {"enabled": True} if enabled_only else {}
        cursor = self._assignments.find(filt, sort=[("priority", ASCENDING)])
        return [QuotaAssignment(**self._doc(d)) async for d in cursor]

    async def create_assignment(self, assignment: QuotaAssignment) -> QuotaAssignment:
        doc = assignment.model_dump(by_alias=False)
        doc["_id"] = assignment.assignment_id
        await self._assignments.insert_one(doc)
        return assignment

    async def update_assignment(
        self, assignment_id: str, updates: dict
    ) -> Optional[QuotaAssignment]:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        count = (
            await self._assignments.update_one({"_id": assignment_id}, {"$set": updates})
        ).modified_count
        if count == 0:
            return None
        return await self.get_assignment(assignment_id)

    async def delete_assignment(self, assignment_id: str) -> bool:
        return (
            await self._assignments.delete_one({"_id": assignment_id})
        ).deleted_count > 0

    # ── Events ─────────────────────────────────────────────────────

    async def record_event(self, event: QuotaEvent) -> QuotaEvent:
        now = datetime.now(timezone.utc)
        doc = event.model_dump(by_alias=False)
        doc["_id"] = event.event_id
        doc["expires_at"] = now + timedelta(days=_EVENT_TTL_DAYS)
        await self._events.insert_one(doc)
        return event

    async def get_user_events(
        self, user_id: str, limit: int = 50, start_time: Optional[str] = None
    ) -> List[QuotaEvent]:
        filt: Dict[str, Any] = {"user_id": user_id}
        if start_time:
            filt["timestamp"] = {"$gte": start_time}
        cursor = self._events.find(filt, sort=[("timestamp", DESCENDING)], limit=limit)
        return [QuotaEvent(**self._doc(d)) async for d in cursor]

    async def get_tier_events(
        self, tier_id: str, limit: int = 100, start_time: Optional[str] = None
    ) -> List[QuotaEvent]:
        filt: Dict[str, Any] = {"tier_id": tier_id}
        if start_time:
            filt["timestamp"] = {"$gte": start_time}
        cursor = self._events.find(filt, sort=[("timestamp", DESCENDING)], limit=limit)
        return [QuotaEvent(**self._doc(d)) async for d in cursor]

    async def get_recent_event(
        self, user_id: str, event_type: str, within_minutes: int = 60
    ) -> Optional[QuotaEvent]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
        ).isoformat()
        doc = await self._events.find_one(
            {"user_id": user_id, "event_type": event_type, "timestamp": {"$gte": cutoff}},
            sort=[("timestamp", DESCENDING)],
        )
        return QuotaEvent(**self._doc(doc)) if doc else None

    # ── Overrides ──────────────────────────────────────────────────

    async def create_override(self, override: QuotaOverride) -> QuotaOverride:
        doc = override.model_dump(by_alias=False)
        doc["_id"] = override.override_id
        await self._events.database[Collections.QUOTA_ASSIGNMENTS].insert_one(
            {"_id": f"override:{override.override_id}", "type": "override", **doc}
        )
        return override

    async def get_override(self, override_id: str) -> Optional[QuotaOverride]:
        doc = await self._assignments.find_one({"_id": f"override:{override_id}"})
        return QuotaOverride(**self._doc(doc, strip_prefix="override:")) if doc else None

    async def get_active_override(self, user_id: str) -> Optional[QuotaOverride]:
        now = datetime.now(timezone.utc).isoformat()
        doc = await self._assignments.find_one(
            {
                "type": "override",
                "user_id": user_id,
                "enabled": True,
                "valid_from": {"$lte": now},
                "valid_until": {"$gte": now},
            }
        )
        return QuotaOverride(**self._doc(doc)) if doc else None

    async def list_overrides(
        self, user_id: Optional[str] = None, active_only: bool = False
    ) -> List[QuotaOverride]:
        filt: Dict[str, Any] = {"type": "override"}
        if user_id:
            filt["user_id"] = user_id
        if active_only:
            now = datetime.now(timezone.utc).isoformat()
            filt["enabled"] = True
            filt["valid_until"] = {"$gte": now}
        cursor = self._assignments.find(filt)
        return [QuotaOverride(**self._doc(d)) async for d in cursor]

    async def update_override(
        self, override_id: str, updates: dict
    ) -> Optional[QuotaOverride]:
        count = (
            await self._assignments.update_one(
                {"_id": f"override:{override_id}"}, {"$set": updates}
            )
        ).modified_count
        if count == 0:
            return None
        return await self.get_override(override_id)

    async def delete_override(self, override_id: str) -> bool:
        return (
            await self._assignments.delete_one({"_id": f"override:{override_id}"})
        ).deleted_count > 0

    # ── Helper ─────────────────────────────────────────────────────

    @staticmethod
    def _doc(doc: dict, strip_prefix: str = "") -> dict:
        d = dict(doc)
        raw_id = d.pop("_id", "")
        if strip_prefix and isinstance(raw_id, str):
            raw_id = raw_id.removeprefix(f"{strip_prefix}:")
        # Map back to the model field name
        if "tier_id" not in d:
            d["tier_id"] = raw_id
        if "assignment_id" not in d:
            d["assignment_id"] = raw_id
        if "event_id" not in d:
            d["event_id"] = raw_id
        if "override_id" not in d:
            d["override_id"] = raw_id
        d.pop("expires_at", None)
        d.pop("type", None)
        return d
