"""MongoDB Motor async client factory.

Configuration via environment variables:
    DATABASE_URL   MongoDB connection string (default: mongodb://localhost:27017)
                   In Azure: swap to Cosmos DB for MongoDB connection string.
    DATABASE_NAME  Database name (default: boise)

The client is a module-level singleton. Call init_connection() at app startup
and close_connection() at shutdown (FastAPI lifespan events).
"""

import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


def _build_client() -> AsyncIOMotorClient:
    url = os.environ.get("DATABASE_URL", "mongodb://localhost:27017")
    # serverSelectionTimeoutMS: fail fast on misconfigured URL rather than
    # hanging for 30 s during startup.
    return AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)


def get_client() -> AsyncIOMotorClient:
    """Return the module-level Motor client, creating it if needed."""
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def get_database() -> AsyncIOMotorDatabase:
    """Return the configured database handle."""
    name = os.environ.get("DATABASE_NAME", "boise")
    return get_client()[name]


async def init_connection() -> None:
    """Initialise the client and verify connectivity.

    Call this from the FastAPI lifespan startup handler so the app fails
    fast if the database is unreachable rather than surfacing errors on
    the first real request.
    """
    global _client
    _client = _build_client()
    db = get_database()
    await db.command("ping")
    url = os.environ.get("DATABASE_URL", "mongodb://localhost:27017")
    name = os.environ.get("DATABASE_NAME", "boise")
    logger.info("MongoDB connected: %s / %s", url.split("@")[-1], name)


async def close_connection() -> None:
    """Close the Motor client. Call from the FastAPI lifespan shutdown handler."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("MongoDB connection closed")
