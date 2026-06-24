"""Chat feature service layer

Contains business logic for chat operations, including agent creation and management.
"""

import logging
import hashlib
import os
import re
from typing import Optional, List, Tuple
from datetime import datetime, timezone

# from agentcore.agent.agent import ChatbotAgent
from agents.main_agent.main_agent import MainAgent
from apis.shared.sessions.models import SessionMetadata
from apis.shared.sessions.metadata import store_session_metadata

logger = logging.getLogger(__name__)


def _hash_tools(tools: Optional[List[str]]) -> str:
    """
    Create a stable hash of the enabled tools list for cache key

    Args:
        tools: List of tool names or None

    Returns:
        Hash string for cache key
    """
    if tools is None:
        return "all_tools"

    # Sort to ensure consistent hash regardless of order
    sorted_tools = sorted(tools)
    tools_str = ",".join(sorted_tools)
    return hashlib.md5(tools_str.encode()).hexdigest()[:8]


def _create_cache_key(
    session_id: str,
    user_id: Optional[str],
    enabled_tools: Optional[List[str]],
    model_id: Optional[str],
    temperature: Optional[float],
    system_prompt: Optional[str],
    caching_enabled: Optional[bool],
    provider: Optional[str],
    max_tokens: Optional[int],
    endpoint_url: Optional[str] = None,
) -> Tuple:
    """Create a cache key for agent instances."""
    tools_hash = _hash_tools(enabled_tools)

    prompt_hash = None
    if system_prompt:
        prompt_hash = hashlib.md5(system_prompt.encode()).hexdigest()[:8]

    return (
        session_id,
        user_id or session_id,
        tools_hash,
        model_id or "default",
        temperature or 0.0,
        prompt_hash,
        caching_enabled or False,
        provider or "bedrock",
        max_tokens or 0,
        endpoint_url or "",
    )


# LRU cache for agent instances
# maxsize=100 allows caching up to 100 different agent configurations
# This reduces initialization overhead for repeated requests
_agent_cache: dict = {}
_CACHE_MAX_SIZE = 100


def get_agent(
    session_id: str,
    user_id: Optional[str] = None,
    auth_token: Optional[str] = None,
    enabled_tools: Optional[List[str]] = None,
    model_id: Optional[str] = None,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None,
    caching_enabled: Optional[bool] = None,
    provider: Optional[str] = None,
    max_tokens: Optional[int] = None,
    endpoint_url: Optional[str] = None,
    api_key: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    databricks_use_invocations: bool = False,
    databricks_responses_api: bool = False,
) -> MainAgent:
    """
    Get or create agent instance with current configuration for session.

    Implements LRU caching to reduce agent initialization overhead.
    Cache key includes all configuration parameters to ensure correct behavior.

    Args:
        session_id: Session identifier
        user_id: User identifier (defaults to session_id)
        enabled_tools: List of tool IDs to enable
        model_id: Model ID (provider-specific format)
        temperature: Model temperature
        system_prompt: System prompt text
        caching_enabled: Whether to enable prompt caching (Bedrock only)
        provider: LLM provider string
        max_tokens: Maximum tokens to generate
        endpoint_url: Custom base URL (OpenAI-compatible providers)
        api_key: Resolved API key for the endpoint
        extra_headers: Additional HTTP headers (e.g. APIM subscription key)

    Returns:
        MainAgent instance (cached or newly created)
    """
    cache_key = _create_cache_key(
        session_id=session_id,
        user_id=user_id,
        enabled_tools=enabled_tools,
        model_id=model_id,
        temperature=temperature,
        system_prompt=system_prompt,
        caching_enabled=caching_enabled,
        provider=provider,
        max_tokens=max_tokens,
        endpoint_url=endpoint_url,
    )

    if cache_key in _agent_cache:
        logger.debug("✅ Agent cache hit")
        return _agent_cache[cache_key]

    logger.debug("⚠️ Agent cache miss - creating new instance")

    agent = MainAgent(
        session_id=session_id,
        user_id=user_id,
        auth_token=auth_token,
        enabled_tools=enabled_tools,
        model_id=model_id,
        temperature=temperature,
        system_prompt=system_prompt,
        caching_enabled=caching_enabled,
        provider=provider,
        max_tokens=max_tokens,
        endpoint_url=endpoint_url,
        api_key=api_key,
        extra_headers=extra_headers,
        databricks_use_invocations=databricks_use_invocations,
        databricks_responses_api=databricks_responses_api,
    )

    # Add to cache with LRU eviction
    if len(_agent_cache) >= _CACHE_MAX_SIZE:
        # Remove oldest entry (first inserted)
        oldest_key = next(iter(_agent_cache))
        del _agent_cache[oldest_key]
        logger.debug(f"🗑️ Evicted oldest agent from cache (size={_CACHE_MAX_SIZE})")

    _agent_cache[cache_key] = agent
    logger.debug("💾 Cached agent")

    return agent


def clear_agent_cache():
    """
    Clear the agent cache

    Useful for testing or when configuration changes require cache invalidation.
    """
    global _agent_cache
    _agent_cache = {}
    logger.info("🗑️ Agent cache cleared")


# ============================================================
# Title Generation
# ============================================================

# System prompt for title generation optimized for Nova Micro
TITLE_GENERATION_SYSTEM_PROMPT = """You are a precise title generator for conversational AI sessions.

Your role is to analyze a user's initial message and create a concise, descriptive title that captures the essence of their intent or question.

Guidelines:
- Maximum 50 characters (strictly enforced)
- Use clear, specific language
- Avoid generic phrases like "Question about" or "Help with"
- Capture the core topic or action
- Use title case (capitalize major words)
- No quotes, periods, or special formatting

Examples:
Input: "Can you help me write a Python script to parse CSV files and extract specific columns?"
Output: Python CSV Parser Script

Input: "I need to understand how React hooks work, specifically useState and useEffect"
Output: React Hooks: useState & useEffect

Input: "What's the weather like in Tokyo right now?"
Output: Tokyo Weather Query

Input: "Help me debug this error: TypeError: Cannot read property 'map' of undefined"
Output: Debug TypeError Map Error

Focus on being informative and scannable. The title should allow users to quickly identify this conversation in a list."""


def _title_heuristic(text: str) -> str:
    """Extract a readable title from the first few meaningful words of user input."""
    # Strip leading filler phrases
    text = re.sub(
        r"^(?:can you|could you|please|help me|i need to|how do i|"
        r"what is|what are|how to|i want to|i would like to)\s+",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    # Take first ~60 characters to work with
    text = text[:60]
    # Remove special characters except common punctuation
    text = re.sub(r"[^\w\s:&\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Title-case and enforce 50-char limit
    title = text.title()
    if len(title) > 50:
        truncated = title[:47].rsplit(" ", 1)[0]
        title = (truncated + "...") if truncated else title[:47] + "..."
    return title or "New Conversation"


async def _call_llm_for_title(user_input: str) -> Optional[str]:
    """Try to generate a title using the first enabled OpenAI-compatible managed model.

    Returns the generated title string, or None if no suitable model is configured
    or if the call fails.
    """
    try:
        import httpx
        from apis.shared.models.managed_models import get_managed_models_service

        service = get_managed_models_service()
        models = await service.list_managed_models(enabled_only=True)

        # Pick first non-Bedrock model with an endpoint URL (OpenAI-compatible)
        model = next(
            (m for m in models if m.provider.lower() != "bedrock" and m.endpoint_url),
            None,
        )
        if not model:
            return None

        api_key = os.environ.get(model.api_key_env_var or "", "") or "none"
        endpoint = model.endpoint_url.rstrip("/")

        payload = {
            "model": model.model_id,
            "messages": [
                {"role": "system", "content": TITLE_GENERATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ],
            "temperature": 0.3,
            "max_tokens": 50,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{endpoint}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            title = data["choices"][0]["message"]["content"].strip()
            if len(title) > 50:
                title = title[:47] + "..."
            return title

    except Exception as exc:
        logger.debug("LLM title generation failed, will use heuristic: %s", exc)
        return None


async def generate_conversation_title(
    session_id: str,
    user_id: str,
    user_input: str
) -> str:
    """
    Generate a conversation title using AWS Bedrock Nova Micro model.

    This function:
    1. Truncates user input to ~500 tokens (2000 chars as rough approximation)
    2. Calls Nova Micro with optimized system prompt
    3. Updates session metadata both locally and in cloud
    4. Returns generated title or fallback on error

    Args:
        session_id: Session identifier
        user_id: User identifier (from JWT)
        user_input: User's first message (will be truncated if needed)

    Returns:
        str: Generated conversation title (max 50 chars) or "New Conversation" on error
    """
    # Truncate input to approximately 500 tokens (~4 chars per token)
    # This keeps the request fast and cost-effective
    MAX_INPUT_LENGTH = 2000
    truncated_input = user_input[:MAX_INPUT_LENGTH]
    if len(user_input) > MAX_INPUT_LENGTH:
        truncated_input += "..."
        logger.debug(f"Truncated input from {len(user_input)} to {MAX_INPUT_LENGTH} chars")

    try:
        logger.info("Generating title for session %s (input length: %d chars)", session_id, len(truncated_input))

        # Try configured LLM first; fall back to heuristic
        title = await _call_llm_for_title(truncated_input)
        if title is None:
            title = _title_heuristic(truncated_input)
            logger.debug("Using heuristic title: %s", title)

        logger.info(f"✅ Generated title: '{title}' for session {session_id}")

        # Update session metadata with the generated title
        # IMPORTANT: We must read existing metadata first and only update the title field.
        # The streaming coordinator has already set message_count correctly, and we must
        # not overwrite it. This function is called async after streaming completes,
        # so there's a race condition where we could overwrite the correct message_count
        # with 0 if we don't preserve existing values.
        from apis.shared.sessions.metadata import get_session_metadata

        logger.info(f"📖 Title generation: Reading existing metadata for session {session_id}")
        existing_metadata = await get_session_metadata(session_id, user_id)

        if existing_metadata:
            logger.info(f"📊 Title generation: Found existing metadata with message_count={existing_metadata.message_count}")
            # Preserve existing metadata, only update title
            session_metadata = SessionMetadata(
                session_id=session_id,
                user_id=user_id,
                title=title,  # Only update this field
                status=existing_metadata.status,
                created_at=existing_metadata.created_at,
                last_message_at=existing_metadata.last_message_at,
                message_count=existing_metadata.message_count,  # PRESERVE existing count
                starred=existing_metadata.starred,
                tags=existing_metadata.tags,
                preferences=existing_metadata.preferences
            )
        else:
            logger.warning(f"⚠️ Title generation: No existing metadata found - creating new with message_count=0")
            # Fallback: If metadata doesn't exist yet (rare edge case), create it
            # The streaming coordinator will update message_count shortly after
            now = datetime.now(timezone.utc).isoformat()
            session_metadata = SessionMetadata(
                session_id=session_id,
                user_id=user_id,
                title=title,
                status="active",
                created_at=now,
                last_message_at=now,
                message_count=0,  # Safe fallback - will be set by streaming coordinator
                starred=False,
                tags=[],
                preferences=None
            )

        logger.info(f"📝 Title generation: About to store metadata with message_count={session_metadata.message_count}")
        await store_session_metadata(
            session_id=session_id,
            user_id=user_id,
            session_metadata=session_metadata
        )

        logger.info(f"💾 Title generation: Stored session metadata with title for session {session_id}")

        return title

    except Exception as e:
        # Log error but don't fail the request
        # Title generation is nice-to-have, not critical
        logger.error(f"Failed to generate title for session {session_id}: {e}", exc_info=True)

        # Return fallback title
        fallback_title = "New Conversation"

        # Still try to store metadata with fallback title
        # Same as above: preserve existing metadata to avoid race conditions
        try:
            from apis.shared.sessions.metadata import get_session_metadata

            existing_metadata = await get_session_metadata(session_id, user_id)

            if existing_metadata:
                # Preserve existing metadata, only update title
                session_metadata = SessionMetadata(
                    session_id=session_id,
                    user_id=user_id,
                    title=fallback_title,
                    status=existing_metadata.status,
                    created_at=existing_metadata.created_at,
                    last_message_at=existing_metadata.last_message_at,
                    message_count=existing_metadata.message_count,  # PRESERVE
                    starred=existing_metadata.starred,
                    tags=existing_metadata.tags,
                    preferences=existing_metadata.preferences
                )
            else:
                # Fallback: metadata doesn't exist yet
                now = datetime.now(timezone.utc).isoformat()
                session_metadata = SessionMetadata(
                    session_id=session_id,
                    user_id=user_id,
                    title=fallback_title,
                    status="active",
                    created_at=now,
                    last_message_at=now,
                    message_count=0,
                    starred=False,
                    tags=[],
                    preferences=None
                )

            await store_session_metadata(
                session_id=session_id,
                user_id=user_id,
                session_metadata=session_metadata
            )
        except Exception as metadata_error:
            logger.error(f"Failed to store fallback metadata: {metadata_error}")

        return fallback_title

