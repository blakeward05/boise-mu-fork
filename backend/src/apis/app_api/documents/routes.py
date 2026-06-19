"""Document management API routes"""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse

from apis.shared.assistants.service import get_assistant
from apis.app_api.documents.models import (
    CreateDocumentRequest,
    DocumentResponse,
    DocumentsListResponse,
    DownloadUrlResponse,
    UploadUrlResponse,
    ReportUploadFailureRequest,
)
from apis.app_api.documents.services.document_service import (
    _generate_document_id,
    create_document,
    list_assistant_documents,
    update_document_status,
    soft_delete_document,
    get_document as get_document_service,
)
from apis.app_api.documents.services.storage_service import (
    _get_storage_key,
    _sanitize_filename,
    generate_download_url,
    generate_upload_url,
)
from apis.shared.auth.dependencies import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistants/{assistant_id}/documents", tags=["documents"])


@router.post("/upload-url", response_model=UploadUrlResponse, status_code=status.HTTP_200_OK)
async def generate_upload_url_endpoint(
    assistant_id: str,
    request: CreateDocumentRequest,
    user_id: str = Depends(get_current_user_id),
) -> UploadUrlResponse:
    """
    Request an upload URL for a document.

    Flow:
    1. Verify user owns the assistant
    2. Generate document_id and storage key
    3. Create document record (status='uploading')
    4. Return upload URL (S3 presigned PUT or local endpoint)
    5. Client PUTs the file to the URL, then calls /{document_id}/complete
    """
    try:
        assistant = await get_assistant(assistant_id, user_id)
        if not assistant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Assistant not found: {assistant_id}")

        document_id = _generate_document_id()
        sanitized_filename = _sanitize_filename(request.filename)
        storage_key = _get_storage_key(assistant_id, document_id, sanitized_filename)

        await create_document(
            assistant_id=assistant_id,
            filename=request.filename,
            content_type=request.content_type,
            size_bytes=request.size_bytes,
            s3_key=storage_key,
            document_id=document_id,
        )

        upload_url, _ = await generate_upload_url(
            assistant_id=assistant_id,
            document_id=document_id,
            filename=request.filename,
            content_type=request.content_type,
            expires_in=3600,
        )

        return UploadUrlResponse(documentId=document_id, uploadUrl=upload_url, expiresIn=3600)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error generating upload URL: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to generate upload URL: {exc}")


@router.put("/{document_id}/source", status_code=status.HTTP_204_NO_CONTENT)
async def upload_document_source(
    assistant_id: str,
    document_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
):
    """
    Receive raw document bytes (local storage mode only).

    The upload-url endpoint returns this URL when DATABASE_URL is set.
    After receiving the file, immediately triggers ingestion as a background task.
    The client does NOT need to call /{document_id}/complete separately in local mode.
    """
    from apis.shared.storage import get_file_storage

    document = await get_document_service(assistant_id, document_id, user_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document not found: {document_id}")

    data = await request.body()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body is empty")

    storage = get_file_storage()
    await storage.write(document.s3_key, data)
    logger.info("Received %d bytes for document %s", len(data), document_id)

    # Trigger ingestion immediately as a background task
    from apis.app_api.documents.services.ingestion_service import ingest_document

    background_tasks.add_task(
        ingest_document,
        assistant_id=assistant_id,
        document_id=document_id,
        storage_key=document.s3_key,
        filename=document.filename,
        content_type=document.content_type,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/complete", response_model=DocumentResponse, status_code=status.HTTP_200_OK)
async def complete_upload(
    assistant_id: str,
    document_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user_id),
) -> DocumentResponse:
    """
    Confirm upload is complete and trigger ingestion (S3/cloud mode).

    Called by the client after successfully uploading to the S3 presigned URL.
    In local mode the PUT /{document_id}/source endpoint triggers ingestion
    automatically, but calling this endpoint is also safe (idempotent).
    """
    document = await get_document_service(assistant_id, document_id, user_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document not found: {document_id}")

    if document.status not in ("uploading", "failed"):
        # Already processing or complete — return current state
        return DocumentResponse.model_validate(document.model_dump(by_alias=True))

    # Trigger ingestion as a background task
    from apis.app_api.documents.services.ingestion_service import ingest_document

    background_tasks.add_task(
        ingest_document,
        assistant_id=assistant_id,
        document_id=document_id,
        storage_key=document.s3_key,
        filename=document.filename,
        content_type=document.content_type,
    )

    # Return immediately with status still 'uploading'; client polls GET /{document_id}
    return DocumentResponse.model_validate(document.model_dump(by_alias=True))


@router.post("/{document_id}/upload-failed", response_model=DocumentResponse, status_code=status.HTTP_200_OK)
async def report_upload_failure(
    assistant_id: str,
    document_id: str,
    request: ReportUploadFailureRequest,
    user_id: str = Depends(get_current_user_id),
) -> DocumentResponse:
    """Report that a client-side upload failed."""
    try:
        document = await get_document_service(assistant_id, document_id, user_id)
        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document not found: {document_id}")

        if document.status != "uploading":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Document is in '{document.status}' state, not 'uploading'. Cannot mark as upload failed.",
            )

        updated = await update_document_status(
            assistant_id=assistant_id,
            document_id=document_id,
            status="failed",
            error_message=request.error or "Upload failed",
            error_details=request.details,
        )

        if not updated:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update document status")

        return DocumentResponse.model_validate(updated.model_dump(by_alias=True))

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error reporting upload failure: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to report upload failure: {exc}")


@router.get("", response_model=DocumentsListResponse, status_code=status.HTTP_200_OK)
async def list_documents(
    assistant_id: str,
    limit: Optional[int] = None,
    next_token: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
) -> DocumentsListResponse:
    """List all documents for an assistant with pagination."""
    try:
        assistant = await get_assistant(assistant_id, user_id)
        if not assistant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Assistant not found: {assistant_id}")

        documents, next_page_token = await list_assistant_documents(
            assistant_id=assistant_id, owner_id=user_id, limit=limit, next_token=next_token
        )

        document_responses = [DocumentResponse.model_validate(doc.model_dump(by_alias=True)) for doc in documents]
        return DocumentsListResponse(documents=document_responses, nextToken=next_page_token)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error listing documents: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list documents: {exc}")


@router.get("/{document_id}", response_model=DocumentResponse, status_code=status.HTTP_200_OK)
async def get_document(
    assistant_id: str,
    document_id: str,
    user_id: str = Depends(get_current_user_id),
) -> DocumentResponse:
    """Get document details and processing status."""
    try:
        document = await get_document_service(assistant_id, document_id, user_id)
        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document not found: {document_id}")
        return DocumentResponse.model_validate(document.model_dump(by_alias=True))

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error retrieving document: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to retrieve document: {exc}")


@router.get("/{document_id}/download", response_model=DownloadUrlResponse, status_code=status.HTTP_200_OK)
async def get_download_url(
    assistant_id: str,
    document_id: str,
    user_id: str = Depends(get_current_user_id),
) -> DownloadUrlResponse:
    """Generate a download URL for the source document."""
    try:
        document = await get_document_service(assistant_id, document_id, user_id)
        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document not found: {document_id}")

        download_url = await generate_download_url(s3_key=document.s3_key, expires_in=3600)
        return DownloadUrlResponse(downloadUrl=download_url, filename=document.filename, expiresIn=3600)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error generating download URL: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to generate download URL: {exc}")


@router.get("/{document_id}/source")
async def download_document_source(
    assistant_id: str,
    document_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """
    Stream source document bytes directly (local storage mode only).

    The download-url endpoint redirects here when DATABASE_URL is set.
    """
    from apis.shared.storage import get_file_storage

    document = await get_document_service(assistant_id, document_id, user_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document not found: {document_id}")

    storage = get_file_storage()
    raw_bytes = await storage.read(document.s3_key)

    return Response(
        content=raw_bytes,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{document.filename}"',
            "Content-Length": str(len(raw_bytes)),
        },
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    assistant_id: str,
    document_id: str,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Delete document using soft-delete + background cleanup pattern."""
    try:
        document = await soft_delete_document(assistant_id, document_id, user_id)
        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document not found: {document_id}")

        import asyncio
        from apis.app_api.documents.services.cleanup_service import cleanup_document_resources

        asyncio.ensure_future(
            cleanup_document_resources(
                document_id=document.document_id,
                assistant_id=assistant_id,
                s3_key=document.s3_key,
                chunk_count=document.chunk_count,
            )
        )

        return None

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error deleting document: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete document: {exc}")
