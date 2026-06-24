"""MongoDB repository for file upload metadata and user storage quotas."""

import logging
from typing import Optional, List, Tuple
from datetime import datetime, timezone, timedelta

from pymongo import DESCENDING

from apis.shared.database import get_database, BaseRepository, Collections
from .models import FileMetadata, UserFileQuota, FileStatus

logger = logging.getLogger(__name__)

_TTL_DAYS = 7

_repository: Optional["FileUploadRepository"] = None


def get_file_upload_repository() -> "FileUploadRepository":
    global _repository
    if _repository is None:
        _repository = FileUploadRepository()
    return _repository


class FileUploadRepository(BaseRepository):
    """MongoDB repository for file metadata and per-user quotas.

    Files document schema (_id = upload_id):
        _id:          upload_id
        user_id:      owner
        session_id:   associated session
        filename:     original filename
        mime_type:    MIME type string
        size_bytes:   int
        storage_key:  path on local filesystem or blob storage key
        storage_bucket: optional bucket/container name
        status:       FileStatus value
        created_at:   datetime
        updated_at:   datetime
        expires_at:   datetime (for TTL index)

    Quotas document schema in user_settings collection (keyed per user):
        Stored as a sub-document inside USER_SETTINGS — avoids a separate collection.
        Alternatively tracked here via a separate "quota" document (_id = f"quota:{user_id}").
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.USER_FILES)
        self._quota_col = get_database()[Collections.USER_SETTINGS]

    # ── File CRUD ──────────────────────────────────────────────────

    async def create_file(self, file_meta: FileMetadata) -> FileMetadata:
        doc = self._meta_to_doc(file_meta)
        await self._insert_one(doc)
        return file_meta

    async def get_file(self, user_id: str, upload_id: str) -> Optional[FileMetadata]:
        doc = await self._find_one({"_id": upload_id, "user_id": user_id})
        return self._doc_to_meta(doc) if doc else None

    async def get_file_by_upload_id(self, upload_id: str) -> Optional[FileMetadata]:
        doc = await self._find_one({"_id": upload_id})
        return self._doc_to_meta(doc) if doc else None

    async def update_file_status(
        self, user_id: str, upload_id: str, status: FileStatus
    ) -> Optional[FileMetadata]:
        status_val = status.value if hasattr(status, "value") else status
        now = datetime.now(timezone.utc)
        count = await self._update_one(
            {"_id": upload_id, "user_id": user_id},
            {"$set": {"status": status_val, "updated_at": now}},
        )
        if count == 0:
            return None
        return await self.get_file(user_id, upload_id)

    async def delete_file(self, user_id: str, upload_id: str) -> Optional[FileMetadata]:
        doc = await self._find_one({"_id": upload_id, "user_id": user_id})
        if not doc:
            return None
        await self._delete_one({"_id": upload_id})
        return self._doc_to_meta(doc)

    async def list_user_files(
        self,
        user_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
        status: Optional[FileStatus] = None,
    ) -> Tuple[List[FileMetadata], Optional[str]]:
        filt: dict = {"user_id": user_id}
        if status:
            filt["status"] = status.value if hasattr(status, "value") else status
        skip = int(cursor) if cursor and cursor.isdigit() else 0
        docs = await self._find_many(
            filt,
            sort=[("created_at", DESCENDING)],
            limit=limit,
            skip=skip,
        )
        items = [self._doc_to_meta(d) for d in docs]
        next_cursor = str(skip + limit) if len(docs) == limit else None
        return items, next_cursor

    async def list_session_files(
        self, session_id: str, status: Optional[FileStatus] = None
    ) -> List[FileMetadata]:
        filt: dict = {"session_id": session_id}
        if status:
            filt["status"] = status.value if hasattr(status, "value") else status
        docs = await self._find_many(filt)
        return [self._doc_to_meta(d) for d in docs]

    async def delete_session_files(self, session_id: str) -> List[FileMetadata]:
        docs = await self._find_many({"session_id": session_id})
        if docs:
            ids = [d["_id"] for d in docs]
            await self._collection.delete_many({"_id": {"$in": ids}})
        return [self._doc_to_meta(d) for d in docs]

    # ── Quota ──────────────────────────────────────────────────────

    async def get_user_quota(self, user_id: str) -> UserFileQuota:
        doc = await self._quota_col.find_one({"_id": f"quota:{user_id}"})
        if not doc:
            return UserFileQuota(
                user_id=user_id,
                total_bytes=0,
                file_count=0,
                updated_at=datetime.now(timezone.utc),
            )
        return UserFileQuota(
            user_id=user_id,
            total_bytes=doc.get("total_bytes", 0),
            file_count=doc.get("file_count", 0),
            updated_at=doc.get("updated_at", datetime.now(timezone.utc)),
        )

    async def increment_quota(self, user_id: str, size_bytes: int) -> UserFileQuota:
        await self._quota_col.update_one(
            {"_id": f"quota:{user_id}"},
            {
                "$inc": {"total_bytes": size_bytes, "file_count": 1},
                "$set": {"updated_at": datetime.now(timezone.utc), "user_id": user_id},
            },
            upsert=True,
        )
        return await self.get_user_quota(user_id)

    async def decrement_quota(self, user_id: str, size_bytes: int) -> UserFileQuota:
        await self._quota_col.update_one(
            {"_id": f"quota:{user_id}"},
            {
                "$inc": {"total_bytes": -size_bytes, "file_count": -1},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
        return await self.get_user_quota(user_id)

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _meta_to_doc(meta: FileMetadata) -> dict:
        status_val = meta.status.value if hasattr(meta.status, "value") else meta.status
        created = meta.created_at if isinstance(meta.created_at, datetime) else datetime.now(timezone.utc)
        return {
            "_id": meta.upload_id,
            "user_id": meta.user_id,
            "session_id": meta.session_id,
            "filename": meta.filename,
            "mime_type": meta.mime_type,
            "size_bytes": meta.size_bytes,
            "storage_key": meta.storage_key,
            "storage_bucket": meta.storage_bucket,
            "status": status_val,
            "created_at": created,
            "updated_at": meta.updated_at if isinstance(meta.updated_at, datetime) else datetime.now(timezone.utc),
            "expires_at": created + timedelta(days=_TTL_DAYS),
        }

    @staticmethod
    def _doc_to_meta(doc: dict) -> FileMetadata:
        return FileMetadata(
            upload_id=doc["_id"],
            user_id=doc["user_id"],
            session_id=doc.get("session_id", ""),
            filename=doc.get("filename", ""),
            mime_type=doc.get("mime_type", ""),
            size_bytes=doc.get("size_bytes", 0),
            storage_key=doc.get("storage_key", ""),
            storage_bucket=doc.get("storage_bucket", ""),
            status=doc.get("status", "pending"),
            created_at=doc.get("created_at", datetime.now(timezone.utc)),
            updated_at=doc.get("updated_at", datetime.now(timezone.utc)),
        )
