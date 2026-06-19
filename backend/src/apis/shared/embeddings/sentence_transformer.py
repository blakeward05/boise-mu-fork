"""
Local SentenceTransformers embedding model.

Default model: all-MiniLM-L6-v2 (384 dims, ~22M params, fast and accurate for English).
Override with EMBEDDING_MODEL_NAME env var for a different HuggingFace model.

Azure OpenAI migration: implement OpenAIEmbeddings(EmbeddingModel) and change
get_embedding_model() to return it — no other code changes required.
"""

import asyncio
import logging
import os
from functools import lru_cache
from typing import List

from .base import EmbeddingModel

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_DIMENSIONS = {
    "all-MiniLM-L6-v2": 384,
    "all-mpnet-base-v2": 768,
    "multi-qa-mpnet-base-dot-v1": 768,
}


@lru_cache(maxsize=1)
def _load_model(model_name: str):
    """Load and cache the SentenceTransformer model (happens once at startup)."""
    from sentence_transformers import SentenceTransformer
    logger.info("Loading SentenceTransformer model: %s", model_name)
    cache_dir = os.environ.get("HF_HOME", None)
    model = SentenceTransformer(model_name, cache_folder=cache_dir)
    logger.info("SentenceTransformer model loaded: %s (%d dims)", model_name, model.get_sentence_embedding_dimension())
    return model


class SentenceTransformerEmbeddings(EmbeddingModel):
    """Local embedding model using SentenceTransformers."""

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or os.environ.get("EMBEDDING_MODEL_NAME", _DEFAULT_MODEL)
        self._dims = _DIMENSIONS.get(self._model_name, 384)

    @property
    def dimensions(self) -> int:
        return self._dims

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        model = _load_model(self._model_name)
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: model.encode(texts, convert_to_numpy=True, show_progress_bar=False),
        )
        return [emb.tolist() for emb in embeddings]
