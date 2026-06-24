"""Managed model data models.

These models define the structure for managed models used across
app API and inference API deployments.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Dict, List, Optional
from datetime import datetime


class ManagedModelCreate(BaseModel):
    """Request model for creating a managed model."""
    model_config = ConfigDict(populate_by_name=True)

    model_id: str = Field(..., alias="modelId", min_length=1)
    model_name: str = Field(..., alias="modelName", min_length=1)
    provider: str = Field(..., min_length=1)
    provider_name: str = Field(..., alias="providerName", min_length=1)
    input_modalities: List[str] = Field(..., alias="inputModalities", min_length=1)
    output_modalities: List[str] = Field(..., alias="outputModalities", min_length=1)
    max_input_tokens: int = Field(..., alias="maxInputTokens", ge=1)
    max_output_tokens: int = Field(..., alias="maxOutputTokens", ge=1)
    # Access control: AppRoles (preferred) or legacy JWT roles
    allowed_app_roles: List[str] = Field(
        default_factory=list,
        alias="allowedAppRoles",
        description="AppRole IDs that can access this model (preferred over availableToRoles)"
    )
    available_to_roles: List[str] = Field(
        default_factory=list,
        alias="availableToRoles",
        description="[DEPRECATED] Legacy JWT role names. Use allowedAppRoles instead. "
                    "During transition, access is granted if user matches EITHER field."
    )
    enabled: bool = True
    input_price_per_million_tokens: float = Field(..., alias="inputPricePerMillionTokens", ge=0)
    output_price_per_million_tokens: float = Field(..., alias="outputPricePerMillionTokens", ge=0)
    cache_write_price_per_million_tokens: Optional[float] = Field(
        None,
        alias="cacheWritePricePerMillionTokens",
        ge=0,
        description="Price per million tokens written to cache (Bedrock only, ~25% markup)"
    )
    cache_read_price_per_million_tokens: Optional[float] = Field(
        None,
        alias="cacheReadPricePerMillionTokens",
        ge=0,
        description="Price per million tokens read from cache (Bedrock only, ~90% discount)"
    )
    is_reasoning_model: bool = Field(False, alias="isReasoningModel")
    knowledge_cutoff_date: Optional[str] = Field(None, alias="knowledgeCutoffDate")
    supports_caching: Optional[bool] = Field(
        None,
        alias="supportsCaching",
        description="Whether this model supports prompt caching. Defaults to True for Bedrock Claude models, False for others."
    )
    is_default: bool = Field(
        False,
        alias="isDefault",
        description="Whether this is the default model for new sessions. Only one model can be default."
    )
    endpoint_url: Optional[str] = Field(
        None,
        alias="endpointUrl",
        description="Base URL for OpenAI-compatible providers (Ollama, vLLM, Databricks, Azure AI, APIM)."
    )
    api_key_env_var: Optional[str] = Field(
        None,
        alias="apiKeyEnvVar",
        description="Name of the environment variable holding the API key for this endpoint."
    )
    extra_headers: Optional[Dict[str, str]] = Field(
        None,
        alias="extraHeaders",
        description="Additional HTTP headers (e.g. Ocp-Apim-Subscription-Key for Azure APIM)."
    )
    databricks_use_invocations: bool = Field(
        False,
        alias="databricksUseInvocations",
        description="Databricks custom serving endpoints use /invocations; foundation models do not."
    )
    databricks_responses_api: bool = Field(
        False,
        alias="databricksResponsesApi",
        description="Endpoint uses Responses API format (input/output); false means Chat Completions format (messages/choices)."
    )


class ManagedModelUpdate(BaseModel):
    """Request model for updating a managed model."""
    model_config = ConfigDict(populate_by_name=True)

    model_id: Optional[str] = Field(None, alias="modelId", min_length=1)
    model_name: Optional[str] = Field(None, alias="modelName")
    provider: Optional[str] = None
    provider_name: Optional[str] = Field(None, alias="providerName")
    input_modalities: Optional[List[str]] = Field(None, alias="inputModalities")
    output_modalities: Optional[List[str]] = Field(None, alias="outputModalities")
    max_input_tokens: Optional[int] = Field(None, alias="maxInputTokens", ge=1)
    max_output_tokens: Optional[int] = Field(None, alias="maxOutputTokens", ge=1)
    # Access control: AppRoles (preferred) or legacy JWT roles
    allowed_app_roles: Optional[List[str]] = Field(
        None,
        alias="allowedAppRoles",
        description="AppRole IDs that can access this model (preferred over availableToRoles)"
    )
    available_to_roles: Optional[List[str]] = Field(
        None,
        alias="availableToRoles",
        description="[DEPRECATED] Legacy JWT role names. Use allowedAppRoles instead."
    )
    enabled: Optional[bool] = None
    input_price_per_million_tokens: Optional[float] = Field(None, alias="inputPricePerMillionTokens", ge=0)
    output_price_per_million_tokens: Optional[float] = Field(None, alias="outputPricePerMillionTokens", ge=0)
    cache_write_price_per_million_tokens: Optional[float] = Field(
        None,
        alias="cacheWritePricePerMillionTokens",
        ge=0,
        description="Price per million tokens written to cache (Bedrock only, ~25% markup)"
    )
    cache_read_price_per_million_tokens: Optional[float] = Field(
        None,
        alias="cacheReadPricePerMillionTokens",
        ge=0,
        description="Price per million tokens read from cache (Bedrock only, ~90% discount)"
    )
    is_reasoning_model: Optional[bool] = Field(None, alias="isReasoningModel")
    knowledge_cutoff_date: Optional[str] = Field(None, alias="knowledgeCutoffDate")
    supports_caching: Optional[bool] = Field(
        None,
        alias="supportsCaching",
        description="Whether this model supports prompt caching."
    )
    is_default: Optional[bool] = Field(
        None,
        alias="isDefault",
        description="Whether this is the default model for new sessions."
    )
    endpoint_url: Optional[str] = Field(None, alias="endpointUrl")
    api_key_env_var: Optional[str] = Field(None, alias="apiKeyEnvVar")
    extra_headers: Optional[Dict[str, str]] = Field(None, alias="extraHeaders")
    databricks_use_invocations: Optional[bool] = Field(None, alias="databricksUseInvocations")
    databricks_responses_api: Optional[bool] = Field(None, alias="databricksResponsesApi")


class ManagedModel(BaseModel):
    """Managed model with full details including cache pricing."""
    model_config = ConfigDict(populate_by_name=True)

    id: str
    model_id: str = Field(..., alias="modelId")
    model_name: str = Field(..., alias="modelName")
    provider: str
    provider_name: str = Field(..., alias="providerName")
    input_modalities: List[str] = Field(..., alias="inputModalities")
    output_modalities: List[str] = Field(..., alias="outputModalities")
    max_input_tokens: int = Field(..., alias="maxInputTokens")
    max_output_tokens: int = Field(..., alias="maxOutputTokens")
    # Access control: AppRoles (preferred) or legacy JWT roles
    allowed_app_roles: List[str] = Field(
        default_factory=list,
        alias="allowedAppRoles",
        description="AppRole IDs that can access this model (preferred over availableToRoles)"
    )
    available_to_roles: List[str] = Field(
        default_factory=list,
        alias="availableToRoles",
        description="[DEPRECATED] Legacy JWT role names. Use allowedAppRoles instead."
    )
    enabled: bool
    input_price_per_million_tokens: float = Field(..., alias="inputPricePerMillionTokens")
    output_price_per_million_tokens: float = Field(..., alias="outputPricePerMillionTokens")
    cache_write_price_per_million_tokens: Optional[float] = Field(
        None,
        alias="cacheWritePricePerMillionTokens",
        description="Price per million tokens written to cache (Bedrock only, ~25% markup)"
    )
    cache_read_price_per_million_tokens: Optional[float] = Field(
        None,
        alias="cacheReadPricePerMillionTokens",
        description="Price per million tokens read from cache (Bedrock only, ~90% discount)"
    )
    is_reasoning_model: bool = Field(..., alias="isReasoningModel")
    knowledge_cutoff_date: Optional[str] = Field(None, alias="knowledgeCutoffDate")
    supports_caching: bool = Field(
        True,
        alias="supportsCaching",
        description="Whether this model supports prompt caching. Defaults to True."
    )
    is_default: bool = Field(
        False,
        alias="isDefault",
        description="Whether this is the default model for new sessions. Only one model can be default."
    )
    endpoint_url: Optional[str] = Field(
        None,
        alias="endpointUrl",
        description="Base URL for OpenAI-compatible providers (Ollama, vLLM, Databricks, Azure AI, APIM)."
    )
    api_key_env_var: Optional[str] = Field(
        None,
        alias="apiKeyEnvVar",
        description="Name of the environment variable holding the API key for this endpoint."
    )
    extra_headers: Optional[Dict[str, str]] = Field(
        None,
        alias="extraHeaders",
        description="Additional HTTP headers (e.g. Ocp-Apim-Subscription-Key for Azure APIM)."
    )
    databricks_use_invocations: bool = Field(
        False,
        alias="databricksUseInvocations",
        description="Databricks custom serving endpoints use /invocations; foundation models do not."
    )
    databricks_responses_api: bool = Field(
        False,
        alias="databricksResponsesApi",
        description="Endpoint uses Responses API format (input/output); false means Chat Completions format (messages/choices)."
    )
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
