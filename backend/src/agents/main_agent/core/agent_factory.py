"""
Factory for creating Strands Agent instances with multi-provider support.

All OpenAI-compatible providers (openai, openai-compatible, databricks,
azure-ai, azure-apim) share the same _create_openai_model() path.
Endpoint URL and API key are resolved upstream and stored on ModelConfig.
"""
import os
import logging
from typing import List, Optional, Any
from strands import Agent
from strands.models import BedrockModel
from strands.models.openai import OpenAIModel
from strands.models.gemini import GeminiModel
from strands.tools.executors import SequentialToolExecutor
from agents.main_agent.core.model_config import ModelConfig, ModelProvider, _OPENAI_COMPATIBLE_PROVIDERS

logger = logging.getLogger(__name__)


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
            client_args["base_url"] = model_config.endpoint_url
            logger.info(
                "Creating OpenAI-compatible model: model_id=%s, base_url=%s",
                model_config.model_id,
                model_config.endpoint_url,
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
