"""
Model configuration for multi-provider LLM support.

Supported providers:
  bedrock          – AWS Bedrock (default)
  openai           – OpenAI (api.openai.com)
  gemini           – Google Gemini
  openai-compatible – Any OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, LocalAI)
  databricks       – Databricks Model Serving (OpenAI-compatible)
  azure-ai         – Azure AI Foundry (OpenAI-compatible)
  azure-apim       – Azure APIM gateway (OpenAI-compatible, subscription key auth)
"""
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class ModelProvider(str, Enum):
    """Supported LLM providers."""
    BEDROCK = "bedrock"
    OPENAI = "openai"
    GEMINI = "gemini"
    OPENAI_COMPATIBLE = "openai-compatible"
    DATABRICKS = "databricks"
    AZURE_AI = "azure-ai"
    AZURE_APIM = "azure-apim"


# Providers that route through the OpenAI-compatible client path
_OPENAI_COMPATIBLE_PROVIDERS = {
    ModelProvider.OPENAI,
    ModelProvider.OPENAI_COMPATIBLE,
    ModelProvider.DATABRICKS,
    ModelProvider.AZURE_AI,
    ModelProvider.AZURE_APIM,
}


@dataclass
class RetryConfig:
    """Configuration for model invocation retry behavior.

    Controls two independent retry layers:
    1. Botocore layer - HTTP-level retries before the Strands SDK sees errors
    2. Strands SDK layer - Agent event loop retries on ModelThrottledException

    When all retries are exhausted, the exception propagates to StreamCoordinator
    which streams it to the client as a conversational error message.

    Can be loaded from environment variables or passed directly.
    """
    # Botocore layer (HTTP-level retries, fires first)
    boto_max_attempts: int = 3          # Total attempts including initial call
    boto_retry_mode: str = "standard"   # "legacy", "standard", or "adaptive"
    connect_timeout: int = 5            # Seconds to wait for connection
    read_timeout: int = 120             # Seconds to wait for response

    # Strands SDK layer (agent event loop retries on ModelThrottledException)
    # Backoff sequence with defaults: 2s, 4s, 8s (3 retries before giving up)
    # Total worst-case wait: ~14s — fast enough for conversational UX
    sdk_max_attempts: int = 4           # Total attempts including initial call
    sdk_initial_delay: float = 2.0      # Seconds before first retry, doubles each retry
    sdk_max_delay: float = 16.0         # Cap on exponential backoff

    @classmethod
    def from_env(cls) -> "RetryConfig":
        """Load configuration from environment variables.

        Environment variables (all optional, defaults shown):
            RETRY_BOTO_MAX_ATTEMPTS=3
            RETRY_BOTO_MODE=standard
            RETRY_CONNECT_TIMEOUT=5
            RETRY_READ_TIMEOUT=120
            RETRY_SDK_MAX_ATTEMPTS=4
            RETRY_SDK_INITIAL_DELAY=2.0
            RETRY_SDK_MAX_DELAY=16.0
        """
        return cls(
            boto_max_attempts=int(os.environ.get("RETRY_BOTO_MAX_ATTEMPTS", "3")),
            boto_retry_mode=os.environ.get("RETRY_BOTO_MODE", "standard"),
            connect_timeout=int(os.environ.get("RETRY_CONNECT_TIMEOUT", "5")),
            read_timeout=int(os.environ.get("RETRY_READ_TIMEOUT", "120")),
            sdk_max_attempts=int(os.environ.get("RETRY_SDK_MAX_ATTEMPTS", "4")),
            sdk_initial_delay=float(os.environ.get("RETRY_SDK_INITIAL_DELAY", "2.0")),
            sdk_max_delay=float(os.environ.get("RETRY_SDK_MAX_DELAY", "16.0")),
        )


@dataclass
class ModelConfig:
    """Configuration for multi-provider LLM models."""
    model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    temperature: float = 0.7
    caching_enabled: bool = True
    provider: ModelProvider = ModelProvider.BEDROCK
    max_tokens: Optional[int] = None
    retry_config: Optional[RetryConfig] = None
    # Custom endpoint config (OpenAI-compatible providers)
    endpoint_url: Optional[str] = None
    api_key: Optional[str] = None          # Already-resolved key (not env var name)
    extra_headers: Optional[Dict[str, str]] = field(default=None)

    def get_provider(self) -> ModelProvider:
        """Detect provider from model_id if not explicitly set."""
        # Explicit non-Bedrock provider always wins
        if self.provider != ModelProvider.BEDROCK:
            return self.provider

        # Auto-detect from model_id patterns when provider is still at default
        model_lower = self.model_id.lower()
        if model_lower.startswith("gpt-") or model_lower.startswith("o1-") or model_lower.startswith("o3-"):
            return ModelProvider.OPENAI
        if model_lower.startswith("gemini-"):
            return ModelProvider.GEMINI

        # Endpoint URL present without explicit provider → generic OpenAI-compatible
        if self.endpoint_url:
            return ModelProvider.OPENAI_COMPATIBLE

        return self.provider

    def is_openai_compatible(self) -> bool:
        """True for all providers that use the OpenAI client protocol."""
        return self.get_provider() in _OPENAI_COMPATIBLE_PROVIDERS

    def to_bedrock_config(self) -> Dict[str, Any]:
        """Convert to BedrockModel configuration dictionary."""
        from strands.models import CacheConfig

        config: Dict[str, Any] = {
            "model_id": self.model_id,
            "temperature": self.temperature,
        }

        # TODO: Re-enable once Bedrock supports cachePoint blocks alongside
        # non-PDF document blocks (.md, .docx, etc.). Currently causes:
        # ValidationException: messages.N.content.M.type: Field required
        # because Bedrock can't translate cachePoint after document blocks
        # to the Anthropic format.
        # See: https://github.com/strands-agents/sdk-python/pull/1438
        # if self.caching_enabled:
        #     config["cache_config"] = CacheConfig(strategy="auto")

        if self.retry_config:
            from botocore.config import Config as BotocoreConfig
            config["boto_client_config"] = BotocoreConfig(
                retries={
                    "max_attempts": self.retry_config.boto_max_attempts,
                    "mode": self.retry_config.boto_retry_mode,
                },
                connect_timeout=self.retry_config.connect_timeout,
                read_timeout=self.retry_config.read_timeout,
            )

        return config

    def to_openai_config(self) -> Dict[str, Any]:
        """Convert to OpenAIModel configuration dictionary.

        Covers OpenAI, OpenAI-compatible (Ollama/vLLM), Databricks,
        Azure AI Foundry, and Azure APIM — all use the OpenAI wire protocol.
        """
        config: Dict[str, Any] = {
            "model_id": self.model_id,
            "params": {
                "temperature": self.temperature,
            },
        }
        if self.max_tokens:
            config["params"]["max_tokens"] = self.max_tokens
        return config

    def to_gemini_config(self) -> Dict[str, Any]:
        """Convert to GeminiModel configuration dictionary."""
        config: Dict[str, Any] = {
            "model_id": self.model_id,
            "params": {
                "temperature": self.temperature,
            },
        }
        if self.max_tokens:
            config["params"]["max_output_tokens"] = self.max_tokens
        return config

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "model_id": self.model_id,
            "temperature": self.temperature,
            "caching_enabled": self.caching_enabled,
            "provider": self.get_provider().value,
            "max_tokens": self.max_tokens,
            "endpoint_url": self.endpoint_url,
        }

    @classmethod
    def from_params(
        cls,
        model_id: Optional[str] = None,
        temperature: Optional[float] = None,
        caching_enabled: Optional[bool] = None,
        provider: Optional[str] = None,
        max_tokens: Optional[int] = None,
        endpoint_url: Optional[str] = None,
        api_key: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> "ModelConfig":
        """Create ModelConfig from optional parameters with defaults applied."""
        provider_enum = ModelProvider.BEDROCK
        if provider:
            try:
                provider_enum = ModelProvider(provider.lower())
            except ValueError:
                pass  # Unknown provider — auto-detect from model_id

        return cls(
            model_id=model_id or cls.model_id,
            temperature=temperature if temperature is not None else cls.temperature,
            caching_enabled=caching_enabled if caching_enabled is not None else cls.caching_enabled,
            provider=provider_enum,
            max_tokens=max_tokens,
            endpoint_url=endpoint_url,
            api_key=api_key,
            extra_headers=extra_headers,
        )
