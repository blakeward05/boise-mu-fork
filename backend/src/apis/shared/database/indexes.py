"""MongoDB collection index definitions.

Call ensure_indexes() once at application startup (inside the FastAPI lifespan
handler, after init_connection()). Indexes are created only if they don't
already exist — safe to call on every restart.

Cosmos DB compatibility:
  All indexes here use MongoDB 4.0-compatible syntax. Cosmos DB for MongoDB
  supports these index types: single-field, compound, sparse, TTL, partial.
  Wildcard and text indexes are NOT used to stay within Cosmos DB limits.
"""

import logging
from pymongo import ASCENDING, DESCENDING, IndexModel

from .connection import get_database
from .collections import Collections

logger = logging.getLogger(__name__)


async def ensure_indexes() -> None:
    """Create all collection indexes. Idempotent — safe to call on every boot."""
    db = get_database()
    created = 0

    specs: dict[str, list[IndexModel]] = {
        Collections.USERS: [
            IndexModel([("email", ASCENDING)], unique=True, name="email_unique"),
            IndexModel([("email_domain", ASCENDING), ("last_login_at", DESCENDING)], name="domain_login"),
            IndexModel([("status", ASCENDING), ("last_login_at", DESCENDING)], name="status_login"),
        ],

        Collections.SESSIONS: [
            IndexModel([("session_id", ASCENDING)], unique=True, name="session_id_unique"),
            IndexModel(
                [("user_id", ASCENDING), ("status", ASCENDING), ("last_message_at", DESCENDING)],
                name="user_status_time",
            ),
            # Soft-deleted sessions: admin queries
            IndexModel([("user_id", ASCENDING), ("deleted", ASCENDING)], name="user_deleted"),
        ],

        Collections.COST_RECORDS: [
            IndexModel([("user_id", ASCENDING), ("timestamp", DESCENDING)], name="user_time"),
            IndexModel([("session_id", ASCENDING), ("timestamp", DESCENDING)], name="session_time"),
            # TTL: auto-delete after 365 days. expires_at is a datetime field.
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_365d"),
        ],

        Collections.USER_COST_SUMMARIES: [
            IndexModel([("user_id", ASCENDING), ("period", ASCENDING)], unique=True, name="user_period_unique"),
        ],

        Collections.SYSTEM_ROLLUPS: [
            IndexModel([("rollup_type", ASCENDING), ("date", ASCENDING)], unique=True, name="type_date_unique"),
        ],

        Collections.QUOTA_TIERS: [
            IndexModel([("tier_id", ASCENDING)], unique=True, name="tier_id_unique"),
        ],

        Collections.QUOTA_ASSIGNMENTS: [
            IndexModel([("user_id", ASCENDING)], unique=True, sparse=True, name="user_id_unique"),
            IndexModel([("role_name", ASCENDING)], sparse=True, name="role_name"),
        ],

        Collections.QUOTA_EVENTS: [
            IndexModel([("user_id", ASCENDING), ("timestamp", DESCENDING)], name="user_time"),
            IndexModel([("event_type", ASCENDING), ("timestamp", DESCENDING)], name="type_time"),
            # TTL: quota events expire after 90 days
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_90d"),
        ],

        Collections.MANAGED_MODELS: [
            IndexModel([("model_id", ASCENDING)], unique=True, name="model_id_unique"),
            IndexModel([("provider", ASCENDING)], name="provider"),
            IndexModel([("enabled", ASCENDING)], name="enabled"),
        ],

        Collections.USER_SETTINGS: [
            IndexModel([("user_id", ASCENDING)], unique=True, name="user_id_unique"),
        ],

        Collections.API_KEYS: [
            IndexModel([("key_hash", ASCENDING)], unique=True, name="key_hash_unique"),
            IndexModel([("user_id", ASCENDING)], name="user_id"),
            IndexModel([("enabled", ASCENDING)], name="enabled"),
        ],

        Collections.APP_ROLES: [
            IndexModel([("role_name", ASCENDING)], unique=True, name="role_name_unique"),
        ],

        Collections.AUTH_PROVIDERS: [
            IndexModel([("provider_id", ASCENDING)], unique=True, name="provider_id_unique"),
            IndexModel([("enabled", ASCENDING)], name="enabled"),
        ],

        Collections.OAUTH_PROVIDERS: [
            IndexModel([("provider_name", ASCENDING)], unique=True, name="provider_name_unique"),
            IndexModel([("enabled", ASCENDING)], name="enabled"),
        ],

        Collections.OAUTH_USER_TOKENS: [
            IndexModel(
                [("user_id", ASCENDING), ("provider_name", ASCENDING)],
                unique=True,
                name="user_provider_unique",
            ),
            # TTL: tokens expire when their expires_at elapses
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_expiry"),
        ],

        Collections.ASSISTANTS: [
            # _id is the assistantId — MongoDB auto-indexes it; no separate unique index needed
            IndexModel([("ownerId", ASCENDING), ("createdAt", DESCENDING)], name="owner_time"),
            IndexModel([("visibility", ASCENDING), ("createdAt", DESCENDING)], name="visibility_time"),
        ],

        Collections.ASSISTANT_SHARES: [
            IndexModel([("assistantId", ASCENDING), ("email", ASCENDING)], unique=True, name="assistant_email_unique"),
            IndexModel([("email", ASCENDING)], name="email"),
        ],

        Collections.USER_FILES: [
            IndexModel([("file_id", ASCENDING)], unique=True, name="file_id_unique"),
            IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_time"),
            IndexModel([("assistant_id", ASCENDING)], sparse=True, name="assistant_id"),
        ],

        Collections.SHARED_CONVERSATIONS: [
            IndexModel([("share_id", ASCENDING)], unique=True, name="share_id_unique"),
            IndexModel([("session_id", ASCENDING)], name="session_id"),
            # TTL: shared links expire after 30 days by default (overridable per doc)
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, sparse=True, name="ttl_expiry"),
        ],

        Collections.RATE_LIMIT_WINDOWS: [
            # TTL: window documents auto-delete when expires_at elapses (2× window duration)
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_expiry"),
        ],
    }

    for collection_name, index_models in specs.items():
        collection = db[collection_name]
        try:
            await collection.create_indexes(index_models)
            created += len(index_models)
        except Exception as exc:
            logger.warning("Index creation warning for %s: %s", collection_name, exc)

    logger.info("MongoDB indexes ensured: %d index specs across %d collections", created, len(specs))
