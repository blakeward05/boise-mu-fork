"""File upload metadata models and allowed file type definitions."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict


class FileStatus(str, Enum):
    PENDING = "pending"  # Upload URL generated, awaiting upload
    READY = "ready"      # Upload complete, file is ready for use
    FAILED = "failed"    # Upload failed or timed out


ALLOWED_MIME_TYPES = {
    # Documents
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
    "text/html": "html",
    "text/csv": "csv",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/markdown": "md",
    # Images
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}

ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".html": "text/html",
    ".csv": "text/csv",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".md": "text/markdown",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def get_file_format(mime_type: str) -> Optional[str]:
    """Return the short format label for a MIME type (e.g. 'pdf')."""
    return ALLOWED_MIME_TYPES.get(mime_type)


def is_allowed_mime_type(mime_type: str) -> bool:
    return mime_type in ALLOWED_MIME_TYPES


class FileMetadata(BaseModel):
    """File metadata stored in MongoDB (user_files collection)."""

    upload_id: str = Field(..., description="Unique identifier (timestamp-prefixed UUID)")
    user_id: str = Field(..., description="Owner user ID")
    session_id: str = Field(..., description="Associated conversation session")

    filename: str = Field(..., description="Original filename")
    mime_type: str = Field(..., description="MIME type")
    size_bytes: int = Field(..., description="File size in bytes")

    # Storage location — provider-agnostic path
    storage_key: str = Field(..., description="Storage path (local filesystem or blob key)")
    storage_bucket: str = Field(default="", description="Bucket/container (empty for local)")

    status: FileStatus = Field(default=FileStatus.PENDING)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(
        use_enum_values=True,
        # Accept legacy s3_key / s3_bucket field names from older documents
        populate_by_name=True,
    )

    @property
    def storage_uri(self) -> str:
        """Provider-agnostic storage URI."""
        if self.storage_bucket:
            return f"storage://{self.storage_bucket}/{self.storage_key}"
        return f"storage://{self.storage_key}"

    # Backward-compat alias so existing callers using .s3_key still work
    @property
    def s3_key(self) -> str:
        return self.storage_key

    @property
    def file_format(self) -> Optional[str]:
        return get_file_format(self.mime_type)


class UserFileQuota(BaseModel):
    user_id: str
    total_bytes: int = Field(default=0)
    file_count: int = Field(default=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# API Request / Response Models
# =============================================================================

class PresignRequest(BaseModel):
    session_id: str = Field(..., validation_alias="sessionId")
    filename: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(..., validation_alias="mimeType")
    size_bytes: int = Field(..., validation_alias="sizeBytes", gt=0)

    model_config = ConfigDict(populate_by_name=True)


class PresignResponse(BaseModel):
    upload_id: str = Field(..., alias="uploadId")
    presigned_url: str = Field(..., alias="presignedUrl")
    expires_at: str = Field(..., alias="expiresAt")

    model_config = ConfigDict(populate_by_name=True)


class CompleteUploadResponse(BaseModel):
    upload_id: str = Field(..., alias="uploadId")
    status: str
    storage_uri: str = Field(..., alias="storageUri")
    filename: str
    size_bytes: int = Field(..., alias="sizeBytes")

    model_config = ConfigDict(populate_by_name=True)


class FileResponse(BaseModel):
    upload_id: str = Field(..., alias="uploadId")
    filename: str
    mime_type: str = Field(..., alias="mimeType")
    size_bytes: int = Field(..., alias="sizeBytes")
    session_id: str = Field(..., alias="sessionId")
    storage_uri: str = Field(..., alias="storageUri")
    status: str
    created_at: str = Field(..., alias="createdAt")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_metadata(cls, meta: FileMetadata) -> "FileResponse":
        return cls(
            upload_id=meta.upload_id,
            filename=meta.filename,
            mime_type=meta.mime_type,
            size_bytes=meta.size_bytes,
            session_id=meta.session_id,
            storage_uri=meta.storage_uri,
            status=meta.status if isinstance(meta.status, str) else meta.status.value,
            created_at=meta.created_at.isoformat() + "Z",
        )


class FileListResponse(BaseModel):
    files: List[FileResponse]
    next_cursor: Optional[str] = Field(None, alias="nextCursor")
    total_count: Optional[int] = Field(None, alias="totalCount")

    model_config = ConfigDict(populate_by_name=True)


class QuotaResponse(BaseModel):
    used_bytes: int = Field(..., alias="usedBytes")
    max_bytes: int = Field(..., alias="maxBytes")
    file_count: int = Field(..., alias="fileCount")

    model_config = ConfigDict(populate_by_name=True)


class QuotaExceededError(BaseModel):
    error: str = "QUOTA_EXCEEDED"
    message: str = "Storage quota exceeded"
    current_usage: int = Field(..., alias="currentUsage")
    max_allowed: int = Field(..., alias="maxAllowed")
    required_space: int = Field(..., alias="requiredSpace")

    model_config = ConfigDict(populate_by_name=True)
