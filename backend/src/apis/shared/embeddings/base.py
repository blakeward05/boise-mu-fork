"""Abstract embedding model interface."""

from abc import ABC, abstractmethod
from typing import List


class EmbeddingModel(ABC):
    """Produce dense vector embeddings for text."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Dimensionality of the embedding vectors produced."""

    @abstractmethod
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of texts and return one vector per text."""

    async def embed_query(self, query: str) -> List[float]:
        """Embed a single query string."""
        results = await self.embed_texts([query])
        return results[0]
