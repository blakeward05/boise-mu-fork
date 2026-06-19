"""
Vector store factory.

Default backend: ChromaVectorStore (local, persistent to LOCAL_CHROMA_PATH).
Cloud migration: implement AzureAISearchVectorStore(VectorStore) and return it here.

Configure via env vars:
  LOCAL_CHROMA_PATH   — Chroma persistence directory (default: ./data/chroma)
  EMBEDDING_PROVIDER  — embedding model to use (default: sentence-transformers)
"""

from .base import VectorStore, VectorSearchResult

_vector_store_instance: "VectorStore | None" = None


def get_vector_store() -> VectorStore:
    """Return the singleton VectorStore for this process."""
    global _vector_store_instance
    if _vector_store_instance is None:
        from apis.shared.embeddings import get_embedding_model
        from .chroma_store import ChromaVectorStore
        dims = get_embedding_model().dimensions
        _vector_store_instance = ChromaVectorStore(embedding_dims=dims)
    return _vector_store_instance


__all__ = ["get_vector_store", "VectorStore", "VectorSearchResult"]
