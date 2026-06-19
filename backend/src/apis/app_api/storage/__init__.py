"""Storage backend factory — always returns MongoStorage.

DATABASE_URL (MongoDB connection string) is required; set it in .env or environment.
"""

import logging

from .metadata_storage import MetadataStorage

logger = logging.getLogger(__name__)


def get_metadata_storage() -> MetadataStorage:
    from .mongo_storage import MongoStorage
    return MongoStorage()


__all__ = [
    "MetadataStorage",
    "get_metadata_storage",
]
