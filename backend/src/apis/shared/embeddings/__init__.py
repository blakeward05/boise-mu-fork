"""
Embedding model factory.

Selection logic (via EMBEDDING_PROVIDER env var, default "sentence-transformers"):
  sentence-transformers  → SentenceTransformerEmbeddings (local, CPU, no cloud needed)
  openai                 → OpenAI-compatible endpoint (configure OPENAI_API_KEY + OPENAI_BASE_URL)

Azure AI migration: set EMBEDDING_PROVIDER=openai and point OPENAI_BASE_URL
at Azure OpenAI — no code changes needed.
"""

import os
from .base import EmbeddingModel

_embedding_model_instance: "EmbeddingModel | None" = None


def get_embedding_model() -> EmbeddingModel:
    """Return the singleton EmbeddingModel for this process."""
    global _embedding_model_instance
    if _embedding_model_instance is None:
        _embedding_model_instance = _build_embedding_model()
    return _embedding_model_instance


def _build_embedding_model() -> EmbeddingModel:
    provider = os.environ.get("EMBEDDING_PROVIDER", "sentence-transformers").lower()

    if provider == "sentence-transformers":
        from .sentence_transformer import SentenceTransformerEmbeddings
        return SentenceTransformerEmbeddings()

    raise ValueError(
        f"Unknown EMBEDDING_PROVIDER: {provider!r}. "
        "Supported values: sentence-transformers"
    )


__all__ = ["get_embedding_model", "EmbeddingModel"]
