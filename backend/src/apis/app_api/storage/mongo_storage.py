"""MongoDB storage implementation — replaces DynamoDBStorage.

Implements the MetadataStorage abstract interface so the rest of the app
is unchanged. Swap get_metadata_storage() to return this instead of
DynamoDBStorage when DATABASE_URL is set.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from pymongo import ASCENDING, DESCENDING

from apis.shared.database import get_database, Collections
from .metadata_storage import MetadataStorage

logger = logging.getLogger(__name__)

_COST_TTL_DAYS = 365
_QUOTA_EVENT_TTL_DAYS = 90


class MongoStorage(MetadataStorage):
    """MongoDB-backed metadata storage."""

    def __init__(self) -> None:
        db = get_database()
        self._cost_records = db[Collections.COST_RECORDS]
        self._cost_summaries = db[Collections.USER_COST_SUMMARIES]
        self._system_rollups = db[Collections.SYSTEM_ROLLUPS]

    # ── Message metadata ───────────────────────────────────────────

    async def store_message_metadata(
        self,
        user_id: str,
        session_id: str,
        message_id: int,
        metadata: Dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        doc = {
            "_id": str(uuid.uuid4()),
            "user_id": user_id,
            "session_id": session_id,
            "message_id": message_id,
            "timestamp": now.isoformat(),
            "expires_at": now + timedelta(days=_COST_TTL_DAYS),
            **{k: v for k, v in metadata.items() if k not in ("_id",)},
        }
        await self._cost_records.insert_one(doc)
        logger.debug("Stored cost record for session=%s msg=%d", session_id, message_id)

    async def get_message_metadata(
        self, user_id: str, session_id: str, message_id: int
    ) -> Optional[Dict[str, Any]]:
        doc = await self._cost_records.find_one(
            {"user_id": user_id, "session_id": session_id, "message_id": message_id}
        )
        return self._clean(doc) if doc else None

    async def get_session_metadata(
        self, user_id: str, session_id: str
    ) -> List[Dict[str, Any]]:
        cursor = self._cost_records.find(
            {"session_id": session_id, "user_id": user_id},
            sort=[("message_id", ASCENDING)],
        )
        return [self._clean(d) async for d in cursor]

    # ── Cost summaries ─────────────────────────────────────────────

    async def get_user_cost_summary(
        self, user_id: str, period: str
    ) -> Optional[Dict[str, Any]]:
        doc = await self._cost_summaries.find_one(
            {"_id": f"{user_id}:{period}"}
        )
        return self._clean(doc) if doc else None

    async def update_user_cost_summary(
        self,
        user_id: str,
        period: str,
        cost_delta: float,
        usage_delta: Dict[str, int],
        timestamp: str,
        model_id: Optional[str] = None,
        model_name: Optional[str] = None,
        cache_savings_delta: float = 0.0,
        provider: Optional[str] = None,
    ) -> None:
        inc: Dict[str, Any] = {
            "total_cost": cost_delta,
            "total_requests": 1,
            "cache_savings": cache_savings_delta,
        }
        for k, v in usage_delta.items():
            inc[f"usage.{k}"] = v

        if model_id:
            safe_key = model_id.replace(".", "_").replace("$", "_")
            inc[f"model_breakdown.{safe_key}.cost"] = cost_delta
            inc[f"model_breakdown.{safe_key}.requests"] = 1

        await self._cost_summaries.update_one(
            {"_id": f"{user_id}:{period}"},
            {
                "$inc": inc,
                "$set": {
                    "user_id": user_id,
                    "period": period,
                    "last_updated": timestamp,
                    "provider": provider or "unknown",
                },
            },
            upsert=True,
        )

    async def get_user_messages_in_range(
        self, user_id: str, start_date: datetime, end_date: datetime
    ) -> List[Dict[str, Any]]:
        cursor = self._cost_records.find(
            {
                "user_id": user_id,
                "timestamp": {
                    "$gte": start_date.isoformat(),
                    "$lte": end_date.isoformat(),
                },
            },
            sort=[("timestamp", ASCENDING)],
        )
        return [self._clean(d) async for d in cursor]

    # ── Admin queries ──────────────────────────────────────────────

    async def get_top_users_by_cost(
        self,
        period: str,
        limit: int = 100,
        min_cost: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        filt: Dict[str, Any] = {"period": period}
        if min_cost is not None:
            filt["total_cost"] = {"$gte": min_cost}
        cursor = self._cost_summaries.find(
            filt, sort=[("total_cost", DESCENDING)], limit=limit
        )
        return [self._clean(d) async for d in cursor]

    # ── System rollups ─────────────────────────────────────────────

    async def track_active_user(
        self, user_id: str, period: str, date: str
    ) -> tuple[bool, bool]:
        """Returns (is_new_today, is_new_this_month)."""
        is_new_today = await self._track_active(f"daily:{date}", user_id)
        is_new_month = await self._track_active(f"monthly:{period}", user_id)
        return is_new_today, is_new_month

    async def track_active_user_for_model(
        self, user_id: str, period: str, model_id: str
    ) -> bool:
        return await self._track_active(f"model:{period}:{model_id}", user_id)

    async def _track_active(self, rollup_id: str, user_id: str) -> bool:
        result = await self._system_rollups.update_one(
            {"_id": rollup_id},
            {"$addToSet": {"active_users": user_id}},
            upsert=True,
        )
        return result.upserted_id is not None or result.modified_count > 0

    async def update_daily_rollup(
        self,
        date: str,
        cost_delta: float,
        usage_delta: Dict[str, int],
        is_new_user: bool = False,
        model_id: Optional[str] = None,
    ) -> None:
        inc: Dict[str, Any] = {"total_cost": cost_delta, "total_requests": 1}
        if is_new_user:
            inc["unique_users"] = 1
        for k, v in usage_delta.items():
            inc[f"usage.{k}"] = v
        await self._system_rollups.update_one(
            {"_id": f"daily:{date}"},
            {"$inc": inc, "$set": {"rollup_type": "daily", "date": date}},
            upsert=True,
        )

    async def update_monthly_rollup(
        self,
        period: str,
        cost_delta: float,
        usage_delta: Dict[str, int],
        cache_savings_delta: float = 0.0,
        is_new_user: bool = False,
        model_id: Optional[str] = None,
    ) -> None:
        inc: Dict[str, Any] = {
            "total_cost": cost_delta,
            "total_requests": 1,
            "cache_savings": cache_savings_delta,
        }
        if is_new_user:
            inc["unique_users"] = 1
        for k, v in usage_delta.items():
            inc[f"usage.{k}"] = v
        await self._system_rollups.update_one(
            {"_id": f"monthly:{period}"},
            {"$inc": inc, "$set": {"rollup_type": "monthly", "period": period}},
            upsert=True,
        )

    async def update_model_rollup(
        self,
        period: str,
        model_id: str,
        model_name: str,
        provider: str,
        cost_delta: float,
        usage_delta: Dict[str, int],
        is_new_user_for_model: bool = False,
    ) -> None:
        inc: Dict[str, Any] = {"total_cost": cost_delta, "total_requests": 1}
        if is_new_user_for_model:
            inc["unique_users"] = 1
        for k, v in usage_delta.items():
            inc[f"usage.{k}"] = v
        await self._system_rollups.update_one(
            {"_id": f"model:{period}:{model_id}"},
            {
                "$inc": inc,
                "$set": {
                    "rollup_type": "model",
                    "period": period,
                    "model_id": model_id,
                    "model_name": model_name,
                    "provider": provider,
                },
            },
            upsert=True,
        )

    async def get_system_summary(
        self, period: str, period_type: str = "monthly"
    ) -> Optional[Dict[str, Any]]:
        doc = await self._system_rollups.find_one(
            {"_id": f"{period_type}:{period}"}
        )
        return self._clean(doc) if doc else None

    async def get_daily_trends(
        self, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        cursor = self._system_rollups.find(
            {
                "rollup_type": "daily",
                "date": {"$gte": start_date, "$lte": end_date},
            },
            sort=[("date", ASCENDING)],
        )
        return [self._clean(d) async for d in cursor]

    async def get_model_usage(self, period: str) -> List[Dict[str, Any]]:
        cursor = self._system_rollups.find(
            {"rollup_type": "model", "period": period},
            sort=[("total_cost", DESCENDING)],
        )
        return [self._clean(d) async for d in cursor]

    @staticmethod
    def _clean(doc: dict) -> dict:
        if doc and "_id" in doc:
            doc = dict(doc)
            doc["_id"] = str(doc["_id"])
        return doc
