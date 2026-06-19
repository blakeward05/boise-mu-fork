"""
File storage factory.

Default backend: LocalFileStorage (local filesystem, path ./data/files).
Cloud migration: implement AzureBlobStorage(FileStorage) and return it here.

Configure via env vars:
  LOCAL_STORAGE_PATH  — base directory for local storage (default: ./data/files)
  APP_URL             — used to construct local upload endpoint URLs
"""

from .file_storage import FileStorage

_storage_instance: "FileStorage | None" = None


def get_file_storage() -> FileStorage:
    """Return the singleton FileStorage backend for this process."""
    global _storage_instance
    if _storage_instance is None:
        from .local_file_storage import LocalFileStorage
        _storage_instance = LocalFileStorage()
    return _storage_instance
