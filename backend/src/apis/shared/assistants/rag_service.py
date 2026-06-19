"""RAG service for assistant knowledge base search and prompt augmentation.

Searches the vector store for assistant-specific knowledge and augments
user prompts with retrieved context chunks.

Works with both local (Chroma) and cloud (S3 Vectors) backends via
get_vector_store() and get_embedding_model().
"""

import logging
from typing import Any, Dict, List

from apis.shared.embeddings import get_embedding_model
from apis.shared.vector_store import get_vector_store

logger = logging.getLogger(__name__)


async def search_assistant_knowledgebase_with_formatting(
    assistant_id: str, query: str, top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    Search assistant knowledge base and return formatted results.

    Returns list of dicts with:
      - text: chunk text
      - distance: similarity distance (lower = more similar)
      - metadata: raw metadata from the vector store
      - key: chunk ID
    """
    try:
        embedding_model = get_embedding_model()
        query_embedding = await embedding_model.embed_query(query)

        vector_store = get_vector_store()
        results = await vector_store.search(
            assistant_id=assistant_id,
            query_embedding=query_embedding,
            top_k=top_k,
        )

        if not results:
            logger.info("No vectors found for assistant %s, query: %.50s", assistant_id, query)
            return []

        # Filter to documents with status='complete' via MongoDB
        results = await _filter_by_document_status(results, assistant_id)

        formatted = [
            {
                "text": r.text,
                "distance": r.distance,
                "metadata": {**r.metadata, "document_id": r.document_id, "source": r.source},
                "key": r.chunk_id,
            }
            for r in results[:top_k]
        ]

        logger.info("Found %d relevant chunks for assistant %s", len(formatted), assistant_id)
        return formatted

    except Exception as exc:
        logger.error("Error searching knowledge base for assistant %s: %s", assistant_id, exc, exc_info=True)
        return []  # Graceful degradation


async def _filter_by_document_status(results, assistant_id: str):
    """Remove chunks whose source document is not in 'complete' status."""
    from apis.shared.database.connection import get_database
    import os

    doc_ids = {r.document_id for r in results if r.document_id}
    if not doc_ids:
        return results

    try:
        db = get_database()
        cursor = db["assistant_documents"].find(
            {"assistant_id": assistant_id, "document_id": {"$in": list(doc_ids)}},
            {"document_id": 1, "status": 1},
        )
        docs = await cursor.to_list(length=None)
        valid_ids = {d["document_id"] for d in docs if d.get("status") == "complete"}
    except Exception as exc:
        logger.warning("Document status filter failed, returning unfiltered results: %s", exc)
        return results

    filtered = [r for r in results if r.document_id in valid_ids]
    if len(filtered) < len(results):
        logger.info(
            "Document status filter: %d → %d chunks (removed %d from non-complete docs)",
            len(results), len(filtered), len(results) - len(filtered),
        )
    return filtered


def augment_prompt_with_context(
    user_message: str,
    context_chunks: List[Dict[str, Any]],
    max_context_length: int = 2000,
) -> str:
    """Prepend retrieved context chunks to the user message."""
    if not context_chunks:
        return user_message

    context_parts: List[str] = []
    total_length = 0

    for i, chunk in enumerate(context_chunks, 1):
        chunk_text = chunk.get("text", "").strip()
        if not chunk_text:
            continue

        chunk_with_header = f"[Context {i}]\n{chunk_text}\n"
        if total_length + len(chunk_with_header) > max_context_length:
            remaining = max_context_length - total_length - len(f"[Context {i}]\n\n")
            if remaining > 0:
                context_parts.append(f"[Context {i}]\n{chunk_text[:remaining]}...\n")
            break

        context_parts.append(chunk_with_header)
        total_length += len(chunk_with_header)

    if not context_parts:
        return user_message

    context_section = "\n".join(context_parts)
    return (
        "The following context is retrieved from the assistant's knowledge base. "
        "Use this information to answer the user's question accurately and comprehensively.\n\n"
        f"{context_section}"
        "---\n"
        f"User Question: {user_message}"
    )
