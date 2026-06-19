"""MongoDB database layer.

Provides Motor async client, base repository, and collection index management.
Swap DATABASE_URL to point at Azure Cosmos DB for MongoDB in production.
"""

from .connection import get_database, get_client, close_connection, init_connection
from .base_repository import BaseRepository
from .collections import Collections

__all__ = [
    "get_database",
    "get_client",
    "close_connection",
    "init_connection",
    "BaseRepository",
    "Collections",
]
