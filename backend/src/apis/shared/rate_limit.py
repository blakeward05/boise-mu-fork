"""Sliding-window rate limiter backed by MongoDB.

Uses atomic $inc on a dedicated rate_limit_windows collection to enforce
per-key request rate limits. TTL index auto-cleans expired window documents.

Fail-open: any database error returns *allowed* so a DB outage never blocks
legitimate traffic.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding-window rate limiter using MongoDB atomic counters."""

    async def check_rate_limit(
        self,
        key_id: str,
        window_seconds: int = 60,
        max_requests: int = 60,
    ) -> bool:
        """Check whether a request is allowed under the rate limit.

        Atomically increments a per-key, per-window counter in MongoDB.
        Returns True if allowed, False if rate-limited.
        Fail-open: returns True on any error.
        """
        try:
            from datetime import datetime, timezone, timedelta
            from apis.shared.database import get_database, Collections

            now = int(time.time())
            window_key = now // window_seconds
            doc_id = f"rl:{key_id}:{window_key}"
            expires_at = datetime.fromtimestamp(
                now + window_seconds * 2, tz=timezone.utc
            )

            from pymongo import ReturnDocument
            db = get_database()
            result = await db[Collections.RATE_LIMIT_WINDOWS].find_one_and_update(
                {"_id": doc_id},
                {
                    "$inc": {"count": 1},
                    "$setOnInsert": {"expires_at": expires_at},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            count = result["count"] if result else 1
            return count <= max_requests

        except Exception as exc:
            logger.warning("Rate limit check failed for key %s: %s", key_id, exc)
            return True  # fail-open


_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
