"""State storage abstraction for OIDC state management."""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class OIDCStateData:
    """Data stored with OIDC state for security validation."""

    redirect_uri: Optional[str] = None
    code_verifier: Optional[str] = None  # PKCE code verifier (S256)
    nonce: Optional[str] = None  # ID token nonce binding
    provider_id: Optional[str] = None  # Auth provider that initiated this flow


class StateStore(ABC):
    """Abstract interface for state token storage."""

    @abstractmethod
    def store_state(
        self,
        state: str,
        data: Optional[OIDCStateData] = None,
        ttl_seconds: int = 600
    ) -> None:
        """
        Store a state token with associated OIDC data.

        Args:
            state: State token to store
            data: Optional OIDC state data (redirect_uri, code_verifier, nonce)
            ttl_seconds: Time-to-live in seconds
        """
        pass

    @abstractmethod
    def get_and_delete_state(self, state: str) -> tuple[bool, Optional[OIDCStateData]]:
        """
        Retrieve and delete a state token (one-time use).

        Args:
            state: State token to retrieve

        Returns:
            Tuple of (is_valid, OIDCStateData or None)
        """
        pass


class InMemoryStateStore(StateStore):
    """In-memory state storage (for single-instance/local development)."""

    def __init__(self):
        """Initialize in-memory storage."""
        # Format: {state: (expires_at, OIDCStateData)}
        self._store: dict[str, tuple[float, Optional[OIDCStateData]]] = {}

    def store_state(
        self,
        state: str,
        data: Optional[OIDCStateData] = None,
        ttl_seconds: int = 600
    ) -> None:
        """Store state in memory."""
        expires_at = time.time() + ttl_seconds
        self._store[state] = (expires_at, data)
        self._cleanup_expired()

    def get_and_delete_state(self, state: str) -> tuple[bool, Optional[OIDCStateData]]:
        """Retrieve and delete state from memory."""
        self._cleanup_expired()

        if state not in self._store:
            return False, None

        expires_at, data = self._store[state]

        # Check expiration
        if time.time() > expires_at:
            del self._store[state]
            return False, None

        # Delete after retrieval (one-time use)
        del self._store[state]
        return True, data

    def _cleanup_expired(self):
        """Remove expired states."""
        current_time = time.time()
        expired = [
            state for state, (expires_at, _) in self._store.items()
            if current_time > expires_at
        ]
        for state in expired:
            del self._store[state]


def create_state_store() -> StateStore:
    """
    Create the appropriate state store.

    Uses InMemoryStateStore for local/single-instance deployments.
    A MongoDB-backed distributed state store will be added in Phase 2.
    """
    logger.info("Using in-memory OIDC state storage")
    return InMemoryStateStore()

