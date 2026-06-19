"""
Chroma vector store implementation.

Persists to LOCAL_CHROMA_PATH (default ./data/chroma).
Uses a single collection ("assistant_knowledge_base") with metadata filtering
on assistant_id — same multi-tenant isolation pattern as S3 Vectors.

Azure AI Search migration: implement AzureAISearchVectorStore(VectorStore)
and change get_vector_store() to return it — no caller changes required.
"""

import asyncio
import logging
import os
from functools import lru_cache
from typing import List, Optional

from .base import VectorSearchResult, VectorStore

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "assistant_knowledge_base"


@lru_cache(maxsize=1)
def _get_chroma_client():
    """Return a persistent Chroma client (created once per process)."""
    import chromadb

    path = os.environ.get("LOCAL_CHROMA_PATH", "./data/chroma")
    os.makedirs(path, exist_ok=True)
    client = chromadb.PersistentClient(path=path)
    logger.info("Chroma client initialised at %s", path)
    return client


def _get_collection(embedding_dims: int):
    """Return (or create) the shared collection."""
    import chromadb.utils.embedding_functions as ef

    client = _get_chroma_client()
    # Use no embedding function — we pre-compute embeddings ourselves
    return client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


class ChromaVectorStore(VectorStore):
    """Local Chroma vector store with persistent storage."""

    def __init__(self, embedding_dims: int = 384) -> None:
        self._dims = embedding_dims

    def _collection(self):
        return _get_collection(self._dims)

    async def add_chunks(
        self,
        assistant_id: str,
        document_id: str,
        chunks: List[str],
        embeddings: List[List[float]],
        source_filename: str,
    ) -> None:
        if not chunks:
            return

        ids = [f"{document_id}#{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "document_id": document_id,
                "assistant_id": assistant_id,
                "source": source_filename,
                "chunk_index": i,
            }
            for i in range(len(chunks))
        ]

        loop = asyncio.get_event_loop()
        collection = self._collection()
        await loop.run_in_executor(
            None,
            lambda: collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=chunks,
                metadatas=metadatas,
            ),
        )
        logger.debug("Stored %d chunks for document %s in Chroma", len(chunks), document_id)

    async def search(
        self,
        assistant_id: str,
        query_embedding: List[float],
        top_k: int = 5,
    ) -> List[VectorSearchResult]:
        loop = asyncio.get_event_loop()
        collection = self._collection()

        results = await loop.run_in_executor(
            None,
            lambda: collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where={"assistant_id": assistant_id},
                include=["documents", "metadatas", "distances"],
            ),
        )

        output: List[VectorSearchResult] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        for chunk_id, text, meta, dist in zip(ids, docs, metas, dists):
            output.append(
                VectorSearchResult(
                    chunk_id=chunk_id,
                    text=text or "",
                    distance=float(dist),
                    document_id=meta.get("document_id", ""),
                    assistant_id=meta.get("assistant_id", ""),
                    source=meta.get("source", ""),
                    metadata=meta,
                )
            )

        return output

    async def delete_document(
        self,
        document_id: str,
        chunk_count: Optional[int] = None,
    ) -> int:
        loop = asyncio.get_event_loop()
        collection = self._collection()

        if chunk_count is not None:
            ids = [f"{document_id}#{i}" for i in range(chunk_count)]
            await loop.run_in_executor(
                None,
                lambda: collection.delete(ids=ids),
            )
            logger.info("Deleted %d chunks for document %s from Chroma", chunk_count, document_id)
            return chunk_count

        # No chunk_count — delete by metadata filter
        existing = await loop.run_in_executor(
            None,
            lambda: collection.get(
                where={"document_id": document_id},
                include=[],
            ),
        )
        ids = existing.get("ids", [])
        if ids:
            await loop.run_in_executor(
                None,
                lambda: collection.delete(ids=ids),
            )
        logger.info("Deleted %d chunks for document %s from Chroma (by filter)", len(ids), document_id)
        return len(ids)

    async def delete_assistant(self, assistant_id: str) -> int:
        loop = asyncio.get_event_loop()
        collection = self._collection()

        existing = await loop.run_in_executor(
            None,
            lambda: collection.get(
                where={"assistant_id": assistant_id},
                include=[],
            ),
        )
        ids = existing.get("ids", [])
        if ids:
            await loop.run_in_executor(
                None,
                lambda: collection.delete(ids=ids),
            )
        logger.info("Deleted %d chunks for assistant %s from Chroma", len(ids), assistant_id)
        return len(ids)
