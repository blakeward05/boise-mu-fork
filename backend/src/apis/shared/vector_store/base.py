"""Abstract vector store interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class VectorSearchResult:
    """Single result from a vector similarity search."""
    chunk_id: str           # e.g. "{document_id}#{chunk_index}"
    text: str
    distance: float         # lower = more similar (cosine distance)
    document_id: str
    assistant_id: str
    source: str             # original filename
    metadata: Dict[str, Any]


class VectorStore(ABC):
    """Abstract backend for storing and searching document chunk embeddings."""

    @abstractmethod
    async def add_chunks(
        self,
        assistant_id: str,
        document_id: str,
        chunks: List[str],
        embeddings: List[List[float]],
        source_filename: str,
    ) -> None:
        """Persist chunk embeddings for a document."""

    @abstractmethod
    async def search(
        self,
        assistant_id: str,
        query_embedding: List[float],
        top_k: int = 5,
    ) -> List[VectorSearchResult]:
        """Return the top-k most similar chunks for the given query embedding."""

    @abstractmethod
    async def delete_document(
        self,
        document_id: str,
        chunk_count: Optional[int] = None,
    ) -> int:
        """Delete all vectors for a document. Returns number of vectors removed."""

    @abstractmethod
    async def delete_assistant(self, assistant_id: str) -> int:
        """Delete all vectors for an assistant. Returns number of vectors removed."""
