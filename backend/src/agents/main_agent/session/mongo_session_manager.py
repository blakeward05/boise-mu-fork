"""
MongoDB Session Manager - Persistent conversation history via MongoDB.

Replaces AgentCore Memory (bedrock_agentcore SDK) with MongoDB for local-first
and Azure Cosmos DB-ready deployments. The connection string swap is the only
change required to move to Cosmos DB for MongoDB in Azure.

Storage layout (sessions collection):
  conversation_messages  – full ordered list of {role, content} message dicts
  compaction_state       – {checkpoint, summary, lastInputTokens, updatedAt}

The Strands SDK hook interface:
  register_hooks()       – registers AgentInitializedEvent + MessageAddedEvent
  message_count          – int property, read by StreamCoordinator before streaming
  update_after_turn()    – async, called by StreamCoordinator after each turn
  flush()                – synchronous emergency save on error
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from agents.main_agent.session.compaction_models import CompactionConfig, CompactionState

logger = logging.getLogger(__name__)

_COLLECTION = "sessions"


class MongoSessionManager:
    """
    Stores conversation history in MongoDB (Motor async + pymongo sync for hook init).

    The Strands SDK fires AgentInitializedEvent synchronously during Agent()
    construction, so the initial load uses the synchronous pymongo client to
    avoid event-loop conflicts. All subsequent persistence uses Motor (async).
    """

    def __init__(
        self,
        session_id: str,
        user_id: str,
        compaction_config: Optional[CompactionConfig] = None,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.compaction_config = compaction_config
        self._messages: List[Dict[str, Any]] = []
        self._compaction_state: CompactionState = CompactionState()

    # ------------------------------------------------------------------
    # StreamCoordinator interface
    # ------------------------------------------------------------------

    @property
    def message_count(self) -> int:
        """Number of messages in the current buffer (history + this-turn messages)."""
        return len(self._messages)

    async def update_after_turn(self, input_tokens: int) -> None:
        """Persist buffered messages and, if over threshold, advance the compaction checkpoint."""
        self._compaction_state.last_input_tokens = input_tokens
        self._compaction_state.updated_at = datetime.now(timezone.utc).isoformat()

        if self._should_compact(input_tokens):
            self._apply_compaction()

        await self._save_session_async()
        logger.debug(
            "Session %s persisted: %d messages, input_tokens=%d",
            self.session_id,
            len(self._messages),
            input_tokens,
        )

    def flush(self) -> Optional[int]:
        """Emergency synchronous save called by StreamCoordinator on error."""
        if not self._messages:
            return 0
        try:
            from pymongo import MongoClient

            url = os.environ.get("DATABASE_URL", "mongodb://localhost:27017")
            db_name = os.environ.get("DATABASE_NAME", "boise")
            client: Any = MongoClient(url, serverSelectionTimeoutMS=5000)
            client[db_name][_COLLECTION].update_one(
                {"_id": self.session_id},
                {"$set": {"conversation_messages": self._messages}},
                upsert=False,
            )
            client.close()
            logger.info(
                "Emergency flush: saved %d messages for session %s",
                len(self._messages),
                self.session_id,
            )
        except Exception as exc:
            logger.error("Emergency flush failed for session %s: %s", self.session_id, exc)
        return len(self._messages)

    # ------------------------------------------------------------------
    # Strands SDK hook interface
    # ------------------------------------------------------------------

    def register_hooks(self, registry, **kwargs) -> None:
        """Register AgentInitializedEvent and MessageAddedEvent callbacks."""
        from strands.hooks import AgentInitializedEvent, MessageAddedEvent

        registry.add_callback(
            AgentInitializedEvent,
            lambda event: self._initialize_agent(event.agent),
        )
        registry.add_callback(
            MessageAddedEvent,
            lambda event: self._on_message_added(event.message, event.agent),
        )
        logger.debug("MongoSessionManager hooks registered for session %s", self.session_id)

    def _initialize_agent(self, agent) -> None:
        """
        Restore conversation history from MongoDB into the agent.

        Called synchronously by Strands during Agent() construction, so this
        uses pymongo (blocking) rather than Motor (async) to avoid running a
        coroutine inside an already-running event loop.
        """
        messages, compaction_state = self._load_session_sync()
        self._messages = messages
        self._compaction_state = compaction_state

        checkpoint = compaction_state.checkpoint
        history = messages[checkpoint:]

        if compaction_state.summary and checkpoint > 0:
            summary_msg: Dict[str, Any] = {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "[Summary of earlier conversation that was compacted: "
                            f"{compaction_state.summary}]"
                        ),
                    }
                ],
            }
            agent.messages = [summary_msg] + list(history)
        else:
            agent.messages = list(history)

        logger.info(
            "Session %s initialised: %d stored messages, checkpoint=%d, agent sees %d",
            self.session_id,
            len(messages),
            checkpoint,
            len(agent.messages),
        )

    def _on_message_added(self, message, agent) -> None:
        """Append each new message to the local buffer as turns progress."""
        if isinstance(message, dict):
            self._messages.append(message)
        else:
            self._messages.append(
                {
                    "role": getattr(message, "role", ""),
                    "content": getattr(message, "content", []),
                }
            )

    # ------------------------------------------------------------------
    # Compaction
    # ------------------------------------------------------------------

    def _should_compact(self, input_tokens: int) -> bool:
        return (
            self.compaction_config is not None
            and self.compaction_config.enabled
            and input_tokens >= self.compaction_config.token_threshold
        )

    def _apply_compaction(self) -> None:
        """Advance the checkpoint to trim the oldest messages from future loads.

        Keeps the most recent `protected_turns` complete turns (2 messages each:
        one user + one assistant) untouched. The full message list is still stored
        in MongoDB; only the checkpoint pointer advances.
        """
        cfg = self.compaction_config
        total = len(self._messages)
        keep_from = max(0, total - cfg.protected_turns * 2)

        if keep_from > self._compaction_state.checkpoint:
            old_checkpoint = self._compaction_state.checkpoint
            self._compaction_state.checkpoint = keep_from
            logger.info(
                "Session %s compacted: checkpoint %d → %d (%d messages trimmed from future loads)",
                self.session_id,
                old_checkpoint,
                keep_from,
                keep_from - old_checkpoint,
            )

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _load_session_sync(self) -> Tuple[List[Dict[str, Any]], CompactionState]:
        """Read session data from MongoDB using the synchronous pymongo driver."""
        from pymongo import MongoClient

        url = os.environ.get("DATABASE_URL", "mongodb://localhost:27017")
        db_name = os.environ.get("DATABASE_NAME", "boise")
        try:
            client: Any = MongoClient(url, serverSelectionTimeoutMS=5000)
            doc = client[db_name][_COLLECTION].find_one(
                {"_id": self.session_id},
                {"conversation_messages": 1, "compaction_state": 1},
            )
            client.close()

            if not doc:
                logger.debug("No existing session document for %s", self.session_id)
                return [], CompactionState()

            messages: List[Dict[str, Any]] = doc.get("conversation_messages") or []
            state = CompactionState.from_dict(doc.get("compaction_state"))
            return messages, state

        except Exception as exc:
            logger.error(
                "Failed to load session %s from MongoDB: %s", self.session_id, exc
            )
            return [], CompactionState()

    async def _save_session_async(self) -> None:
        """Persist messages and compaction state using the Motor async client."""
        from apis.shared.database.connection import get_database

        db = get_database()
        await db[_COLLECTION].update_one(
            {"_id": self.session_id},
            {
                "$set": {
                    "conversation_messages": self._messages,
                    "compaction_state": self._compaction_state.to_dict(),
                }
            },
            upsert=False,
        )
