"""Abstract base repository for MongoDB collections.

All concrete repository classes inherit from this. It provides:
  - A typed reference to the Motor collection
  - Helpers for common operations (_find_one, _find_many, _upsert)
  - Consistent ObjectId / string _id handling

Cosmos DB compatibility note:
  Cosmos DB for MongoDB treats the document `id` field as the partition key
  by default. We always set _id explicitly so the mapping is predictable.
"""

import logging
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from pymongo import ReturnDocument

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base class for all MongoDB repositories."""

    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str) -> None:
        self._collection: AsyncIOMotorCollection = db[collection_name]

    @property
    def collection(self) -> AsyncIOMotorCollection:
        return self._collection

    # ------------------------------------------------------------------
    # Protected helpers — use these in subclasses, not raw Motor calls,
    # so error handling and logging stay consistent.
    # ------------------------------------------------------------------

    async def _find_one(
        self, filter: dict, projection: Optional[dict] = None
    ) -> Optional[dict]:
        doc = await self._collection.find_one(filter, projection)
        if doc:
            doc = self._normalise(doc)
        return doc

    async def _find_many(
        self,
        filter: dict,
        projection: Optional[dict] = None,
        sort: Optional[list] = None,
        limit: int = 0,
        skip: int = 0,
    ) -> list[dict]:
        cursor = self._collection.find(filter, projection)
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        docs = await cursor.to_list(length=None if limit == 0 else limit)
        return [self._normalise(d) for d in docs]

    async def _upsert(self, filter: dict, document: dict) -> dict:
        """Insert or replace a document matching filter. Returns the saved doc."""
        result = await self._collection.find_one_and_replace(
            filter,
            document,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return self._normalise(result)

    async def _insert_one(self, document: dict) -> dict:
        await self._collection.insert_one(document)
        return self._normalise(document)

    async def _update_one(self, filter: dict, update: dict, upsert: bool = False) -> int:
        result = await self._collection.update_one(filter, update, upsert=upsert)
        return result.modified_count

    async def _delete_one(self, filter: dict) -> int:
        result = await self._collection.delete_one(filter)
        return result.deleted_count

    async def _count(self, filter: dict) -> int:
        return await self._collection.count_documents(filter)

    @staticmethod
    def _normalise(doc: dict) -> dict:
        """Convert ObjectId _id to string for JSON serialisation."""
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc

    async def _atomic_increment(
        self, filter: dict, increments: dict[str, Any], upsert: bool = True
    ) -> None:
        """Atomically increment numeric fields. Safe for concurrent writers."""
        await self._collection.update_one(
            filter,
            {"$inc": increments},
            upsert=upsert,
        )
