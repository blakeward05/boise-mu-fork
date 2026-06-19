"""Metadata storage for messages and sessions — MongoDB implementation.

The MetadataStorage abstraction (MongoStorage) handles cost records and
summaries; Motor handles session documents in the sessions collection.
"""

import logging
import base64
from typing import Optional, Tuple, Any, Dict
from datetime import datetime, timezone

from apis.shared.database import get_database, Collections
from .models import MessageMetadata, SessionMetadata
from agents.main_agent.session.preview_session_manager import is_preview_session

logger = logging.getLogger(__name__)


async def store_message_metadata(
    session_id: str,
    user_id: str,
    message_id: int,
    message_metadata: MessageMetadata,
) -> None:
    from fastapi import HTTPException
    from apis.shared.errors import ErrorCode, create_error_response
    from apis.app_api.storage import get_metadata_storage

    try:
        storage = get_metadata_storage()
        metadata_dict = message_metadata.model_dump(by_alias=True, exclude_none=True)
        await storage.store_message_metadata(user_id, session_id, message_id, metadata_dict)

        timestamp = metadata_dict.get("attribution", {}).get(
            "timestamp", datetime.now(timezone.utc).isoformat()
        )
        await _update_cost_summary_async(
            user_id=user_id,
            timestamp=timestamp,
            message_metadata=message_metadata,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to store message metadata: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=create_error_response(
                code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Failed to store message metadata in database",
                detail=str(e),
            ),
        )


async def store_user_display_text(
    session_id: str,
    user_id: str,
    message_id: int,
    display_text: str,
) -> None:
    if is_preview_session(session_id):
        return
    try:
        db = get_database()
        await db[Collections.COST_RECORDS].update_one(
            {"session_id": session_id, "message_id": message_id, "record_type": "display_text"},
            {"$set": {"user_id": user_id, "display_text": display_text, "record_type": "display_text"}},
            upsert=True,
        )
    except Exception as e:
        logger.error(f"Failed to store user displayText: {e}", exc_info=True)


async def store_session_metadata(
    session_id: str,
    user_id: str,
    session_metadata: SessionMetadata,
) -> None:
    from fastapi import HTTPException
    from apis.shared.errors import ErrorCode, create_error_response

    try:
        db = get_database()
        doc = session_metadata.model_dump(by_alias=True, exclude_none=True)
        doc["_id"] = session_id
        await db[Collections.SESSIONS].update_one(
            {"_id": session_id},
            {"$set": doc},
            upsert=True,
        )
    except Exception as e:
        logger.error(f"Failed to store session metadata: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=create_error_response(
                code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Failed to store session metadata in database",
                detail=str(e),
            ),
        )


async def get_session_metadata(session_id: str, user_id: str) -> Optional[SessionMetadata]:
    try:
        db = get_database()
        doc = await db[Collections.SESSIONS].find_one({"_id": session_id, "user_id": user_id})
        if not doc:
            return None
        doc["session_id"] = str(doc.pop("_id"))
        return SessionMetadata.model_validate(doc)
    except Exception as e:
        logger.error(f"Failed to retrieve session metadata: {e}", exc_info=True)
        return None


async def get_all_message_metadata(session_id: str, user_id: str) -> Dict[str, Any]:
    try:
        db = get_database()
        metadata_index: dict = {}

        # Cost records (exclude display_text records)
        async for doc in db[Collections.COST_RECORDS].find(
            {"session_id": session_id, "user_id": user_id, "record_type": {"$ne": "display_text"}}
        ):
            raw_id = doc.get("message_id", 0)
            message_id = str(int(raw_id)) if isinstance(raw_id, (int, float)) else str(raw_id)
            clean = {
                k: v for k, v in doc.items()
                if k not in ("_id", "session_id", "user_id", "message_id", "record_type")
            }
            metadata_index[message_id] = clean

        # Display text records
        async for doc in db[Collections.COST_RECORDS].find(
            {"session_id": session_id, "user_id": user_id, "record_type": "display_text"}
        ):
            raw_id = doc.get("message_id", 0)
            message_id = str(int(raw_id)) if isinstance(raw_id, (int, float)) else str(raw_id)
            display_text = doc.get("display_text")
            if display_text:
                if message_id in metadata_index:
                    metadata_index[message_id]["displayText"] = display_text
                else:
                    metadata_index[message_id] = {"displayText": display_text}

        return metadata_index
    except Exception as e:
        logger.error(f"Failed to query message metadata: {e}", exc_info=True)
        return {}


async def list_user_sessions(
    user_id: str,
    limit: Optional[int] = None,
    next_token: Optional[str] = None,
) -> Tuple[list[SessionMetadata], Optional[str]]:
    from fastapi import HTTPException
    from apis.shared.errors import ErrorCode, create_error_response

    try:
        db = get_database()

        skip = 0
        if next_token:
            try:
                skip = int(base64.b64decode(next_token).decode("utf-8"))
            except Exception:
                skip = 0

        filt = {"user_id": user_id, "status": {"$ne": "deleted"}}
        sort = [("last_message_at", -1)]

        fetch_limit = (limit + 1) if limit else None
        cursor = db[Collections.SESSIONS].find(filt, sort=sort, skip=skip)
        if fetch_limit:
            cursor = cursor.limit(fetch_limit)

        sessions: list[SessionMetadata] = []
        async for doc in cursor:
            session_id = str(doc.get("_id", ""))
            if is_preview_session(session_id):
                continue
            doc["session_id"] = session_id
            doc.pop("_id", None)
            try:
                sessions.append(SessionMetadata.model_validate(doc))
            except Exception as e:
                logger.warning(f"Failed to parse session: {e}")

        next_page_token = None
        if limit and len(sessions) > limit:
            sessions = sessions[:limit]
            next_page_token = base64.b64encode(str(skip + limit).encode()).decode()

        return sessions, next_page_token

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list user sessions: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=create_error_response(
                code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Failed to list user sessions from database",
                detail=str(e),
            ),
        )


async def _update_cost_summary_async(
    user_id: str,
    timestamp: str,
    message_metadata: MessageMetadata,
) -> None:
    try:
        import asyncio

        cost = message_metadata.cost or 0.0
        token_usage = message_metadata.token_usage
        cache_read_tokens = 0
        usage_delta: dict = {}
        if token_usage:
            cache_read_tokens = token_usage.cache_read_input_tokens or 0
            usage_delta = {
                "inputTokens": token_usage.input_tokens or 0,
                "outputTokens": token_usage.output_tokens or 0,
                "cacheReadInputTokens": cache_read_tokens,
                "cacheWriteInputTokens": token_usage.cache_write_input_tokens or 0,
            }

        model_id = model_name = provider = None
        if message_metadata.model_info:
            model_id = message_metadata.model_info.model_id
            model_name = message_metadata.model_info.model_name
            provider = message_metadata.model_info.provider

        cache_savings = 0.0
        if cache_read_tokens > 0 and message_metadata.model_info:
            pricing = message_metadata.model_info.pricing_snapshot
            if pricing:
                p = pricing.model_dump(by_alias=True) if hasattr(pricing, "model_dump") else pricing
                input_price = p.get("inputPricePerMtok", 0)
                cache_read_price = p.get("cacheReadPricePerMtok", 0)
                cache_savings = (cache_read_tokens / 1_000_000) * (input_price - cache_read_price)

        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            period = dt.strftime("%Y-%m")
            date = dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            now = datetime.now(timezone.utc)
            period = now.strftime("%Y-%m")
            date = now.strftime("%Y-%m-%d")

        from apis.app_api.storage import get_metadata_storage
        storage = get_metadata_storage()

        await storage.update_user_cost_summary(
            user_id=user_id,
            period=period,
            cost_delta=cost,
            usage_delta=usage_delta,
            timestamp=timestamp,
            model_id=model_id,
            model_name=model_name,
            cache_savings_delta=cache_savings,
            provider=provider,
        )

        asyncio.create_task(
            _update_system_rollups_async(
                user_id=user_id,
                period=period,
                date=date,
                cost=cost,
                usage_delta=usage_delta,
                cache_savings=cache_savings,
                model_id=model_id,
                model_name=model_name,
                provider=provider,
            )
        )
    except Exception as e:
        logger.error(f"Failed to update cost summary (non-critical): {e}", exc_info=True)


async def _update_system_rollups_async(
    user_id: str,
    period: str,
    date: str,
    cost: float,
    usage_delta: dict,
    cache_savings: float,
    model_id: Optional[str],
    model_name: Optional[str],
    provider: Optional[str],
) -> None:
    try:
        from apis.app_api.storage import get_metadata_storage
        storage = get_metadata_storage()

        is_new_today, is_new_this_month = await storage.track_active_user(
            user_id=user_id, period=period, date=date
        )
        await storage.update_daily_rollup(
            date=date, cost_delta=cost, usage_delta=usage_delta, is_new_user=is_new_today, model_id=model_id
        )
        await storage.update_monthly_rollup(
            period=period,
            cost_delta=cost,
            usage_delta=usage_delta,
            cache_savings_delta=cache_savings,
            is_new_user=is_new_this_month,
            model_id=model_id,
        )
        if model_id and model_name and provider:
            await storage.update_model_rollup(
                period=period,
                model_id=model_id,
                model_name=model_name,
                provider=provider,
                cost_delta=cost,
                usage_delta=usage_delta,
                is_new_user_for_model=False,
            )
    except Exception as e:
        logger.error(f"Failed to update system rollups (non-critical): {e}", exc_info=True)
