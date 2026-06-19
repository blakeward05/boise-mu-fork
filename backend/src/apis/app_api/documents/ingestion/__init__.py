"""
Document ingestion pipeline.

In local mode: documents are ingested in-process via
apis.app_api.documents.services.ingestion_service.ingest_document(),
called as a FastAPI BackgroundTask after upload.

Processors (Docling, CSV chunker) in processors/ are used by both the
in-process service and any future cloud ingestion paths.
"""
