"""
Factory for creating Strands Agent instances with multi-provider support.

All OpenAI-compatible providers (openai, openai-compatible, databricks,
azure-ai, azure-apim) share the same _create_openai_model() path.
Endpoint URL and API key are resolved upstream and stored on ModelConfig.
"""
import os
import json
import time
import uuid
import logging
from typing import List, Optional, Any
import httpx
from strands import Agent
from strands.models import BedrockModel
from strands.models.openai import OpenAIModel
from strands.models.gemini import GeminiModel
from strands.tools.executors import SequentialToolExecutor
from agents.main_agent.core.model_config import ModelConfig, ModelProvider, _OPENAI_COMPATIBLE_PROVIDERS

logger = logging.getLogger(__name__)


class _DatabricksTransport(httpx.AsyncBaseTransport):
    """Rewrites /v1/chat/completions → /invocations and handles Responses API ↔ SSE conversion."""

    def __init__(self, wrapped: httpx.AsyncBaseTransport, responses_api: bool = False) -> None:
        self._wrapped = wrapped
        self._responses_api = responses_api

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # OpenAI SDK appends v1/chat/completions relative to base_url.
        if url.endswith("/v1/chat/completions"):
            new_url = url[: -len("/v1/chat/completions")] + "/invocations"
        elif url.endswith("/chat/completions"):
            new_url = url[: -len("/chat/completions")] + "/invocations"
        else:
            return await self._wrapped.handle_async_request(request)

        # Rewrite request body based on endpoint format
        body = json.loads(request.content)
        was_streaming = bool(body.pop("stream", False))
        # Log message count and first-message content type for diagnosis
        msgs = body.get("messages") or body.get("input") or []
        logger.info("Transport: %d messages, first content type=%r", len(msgs),
                    type(msgs[0].get("content") if msgs else None).__name__)
        if msgs:
            first_content = msgs[0].get("content")
            if isinstance(first_content, list):
                logger.info("  first msg content blocks: %s",
                            [b.get("type") or list(b.keys()) for b in first_content[:5]])
            elif isinstance(first_content, str):
                logger.info("  first msg content str len=%d", len(first_content))
            else:
                logger.info("  first msg content=%r", first_content)
        if self._responses_api and "messages" in body:
            body["input"] = body.pop("messages")
        new_content = json.dumps(body).encode()
        headers = {k: v for k, v in request.headers.items()}
        headers["content-length"] = str(len(new_content))

        new_request = httpx.Request(
            method=request.method,
            url=new_url,
            headers=headers,
            content=new_content,
        )
        response = await self._wrapped.handle_async_request(new_request)
        logger.info(
            "Databricks response: status=%s content-type=%s",
            response.status_code,
            response.headers.get("content-type", "unknown"),
        )

        if not was_streaming or response.status_code != 200:
            return response

        # Buffer the response body before reading (httpx streaming response is not auto-read)
        await response.aread()

        # Convert JSON response to fake SSE so the OpenAI SDK can parse it
        try:
            data = json.loads(response.content)
            content = ""
            if "output" in data:
                # OpenAI Responses API format — model ran its own tools internally;
                # collect all assistant output_text blocks as the final response.
                parts = []
                for item in data["output"]:
                    if item.get("type") == "message" and item.get("role") == "assistant":
                        for block in item.get("content", []):
                            if block.get("type") == "output_text":
                                parts.append(block.get("text", ""))
                content = "\n\n".join(parts)
            elif "choices" in data:
                msg = data["choices"][0].get("message", {})
                content = msg.get("content", "")
            elif "predictions" in data:
                preds = data["predictions"]
                if isinstance(preds, str):
                    content = preds
                elif isinstance(preds, list) and preds:
                    first = preds[0]
                    content = first.get("content", str(first)) if isinstance(first, dict) else str(first)
            else:
                content = str(data)
        except Exception:
            content = response.content.decode("utf-8", errors="replace")

        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())
        chunks = [
            {"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": "databricks",
             "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
            {"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": "custom",
             "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]},
            {"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": "custom",
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            content=sse.encode("utf-8"),
        )


class _PersistentAsyncClient(httpx.AsyncClient):
    """AsyncClient that ignores close calls so the OpenAI SDK cannot close it between calls."""

    async def aclose(self) -> None:
        pass

    def close(self) -> None:
        pass


class AgentFactory:
    """Factory for creating configured Strands Agent instances with multi-provider support."""

    @staticmethod
    def _create_bedrock_model(model_config: ModelConfig) -> BedrockModel:
        bedrock_config = model_config.to_bedrock_config()
        return BedrockModel(**bedrock_config)

    @staticmethod
    def _create_openai_model(model_config: ModelConfig) -> OpenAIModel:
        """
        Create an OpenAIModel for any OpenAI-compatible endpoint.

        API key resolution priority:
          1. model_config.api_key  (resolved from managed model's api_key_env_var)
          2. OPENAI_API_KEY env var (fallback for vanilla OpenAI)

        Endpoint URL:
          model_config.endpoint_url overrides the default api.openai.com base.
          Required for Ollama, vLLM, Databricks, Azure AI Foundry, and APIM.
        """
        api_key = (
            model_config.api_key
            or os.getenv("OPENAI_API_KEY")
            or "no-key"  # Ollama and some local servers accept any non-empty string
        )

        openai_config = model_config.to_openai_config()
        client_args: dict = {"api_key": api_key}

        if model_config.endpoint_url:
            base_url = model_config.endpoint_url.rstrip("/")

            if model_config.databricks_use_invocations:
                # Custom /invocations endpoint: strip trailing /invocations so the OpenAI
                # SDK can append its path, then the transport rewrites back to /invocations.
                if base_url.endswith("/invocations"):
                    base_url = base_url[: -len("/invocations")]
                client_args["http_client"] = _PersistentAsyncClient(
                    transport=_DatabricksTransport(
                        httpx.AsyncHTTPTransport(),
                        responses_api=model_config.databricks_responses_api,
                    )
                )

            client_args["base_url"] = base_url
            logger.info(
                "Creating OpenAI-compatible model: model_id=%s, base_url=%s, use_invocations=%s, responses_api=%s",
                model_config.model_id,
                base_url,
                model_config.databricks_use_invocations,
                model_config.databricks_responses_api,
            )
        else:
            logger.info("Creating OpenAI model: model_id=%s", model_config.model_id)

        if model_config.extra_headers:
            client_args["default_headers"] = model_config.extra_headers

        return OpenAIModel(client_args=client_args, **openai_config)

    @staticmethod
    def _create_gemini_model(model_config: ModelConfig) -> GeminiModel:
        api_key = os.getenv("GOOGLE_GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GOOGLE_GEMINI_API_KEY environment variable is required for Gemini models."
            )
        gemini_config = model_config.to_gemini_config()
        client_args = {"api_key": api_key}
        logger.info("Creating Gemini model: model_id=%s", model_config.model_id)
        return GeminiModel(client_args=client_args, **gemini_config)

    @staticmethod
    def create_agent(
        model_config: ModelConfig,
        system_prompt: str,
        tools: List[Any],
        session_manager: Any,
        hooks: Optional[List[Any]] = None,
    ) -> Agent:
        """
        Create a Strands Agent instance with the appropriate model provider.

        Raises:
            ValueError: If provider is unsupported or required API keys are missing.
        """
        provider = model_config.get_provider()
        logger.info(
            "Creating agent: provider=%s, model_id=%s", provider.value, model_config.model_id
        )

        if provider == ModelProvider.BEDROCK:
            model = AgentFactory._create_bedrock_model(model_config)
        elif provider in _OPENAI_COMPATIBLE_PROVIDERS:
            model = AgentFactory._create_openai_model(model_config)
        elif provider == ModelProvider.GEMINI:
            model = AgentFactory._create_gemini_model(model_config)
        else:
            raise ValueError(f"Unsupported model provider: {provider}")

        # SDK-level retry strategy — only Bedrock; other providers handle retries internally
        retry_strategy = None
        if provider == ModelProvider.BEDROCK and model_config.retry_config:
            from strands import ModelRetryStrategy
            retry_strategy = ModelRetryStrategy(
                max_attempts=model_config.retry_config.sdk_max_attempts,
                initial_delay=model_config.retry_config.sdk_initial_delay,
                max_delay=model_config.retry_config.sdk_max_delay,
            )
            logger.info(
                "Retry strategy: boto=%d (%s), sdk=%d (%.1fs–%.1fs backoff)",
                model_config.retry_config.boto_max_attempts,
                model_config.retry_config.boto_retry_mode,
                model_config.retry_config.sdk_max_attempts,
                model_config.retry_config.sdk_initial_delay,
                model_config.retry_config.sdk_max_delay,
            )

        # Use SequentialToolExecutor to prevent concurrent browser operations
        # (prevents "Failed to start and initialize Playwright" with NovaAct)
        return Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            tool_executor=SequentialToolExecutor(),
            session_manager=session_manager,
            hooks=hooks if hooks else None,
            retry_strategy=retry_strategy,
        )
