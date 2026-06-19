/**
 * Available model providers.
 */
export type ModelProvider =
  | 'bedrock'
  | 'openai'
  | 'openai-compatible'
  | 'gemini'
  | 'databricks'
  | 'azure-ai-foundry'
  | 'azure-apim';

/**
 * Available model providers as a constant array (most useful first for local-first setup).
 */
export const AVAILABLE_PROVIDERS: ModelProvider[] = [
  'openai-compatible',
  'openai',
  'bedrock',
  'gemini',
  'databricks',
  'azure-ai-foundry',
  'azure-apim',
];

/**
 * Human-readable labels for each provider.
 */
export const PROVIDER_LABELS: Record<ModelProvider, string> = {
  'openai-compatible': 'OpenAI Compatible (Ollama, vLLM, LM Studio…)',
  'openai': 'OpenAI',
  'bedrock': 'AWS Bedrock',
  'gemini': 'Google Gemini',
  'databricks': 'Databricks',
  'azure-ai-foundry': 'Azure AI Foundry',
  'azure-apim': 'Azure APIM',
};

/**
 * Represents a managed model in the system.
 * This extends the Bedrock foundation model with additional metadata
 * for role-based access control and pricing.
 */
export interface ManagedModel {
  /** Unique identifier for the model */
  id: string;
  /** Bedrock model ID */
  modelId: string;
  /** Human-readable name of the model */
  modelName: string;
  /** Model provider (AWS, OpenAI, Google) */
  provider: ModelProvider;
  /** Provider name (e.g., 'Anthropic', 'Amazon', 'Meta') */
  providerName: string;
  /** List of supported input modalities (e.g., 'TEXT', 'IMAGE') */
  inputModalities: string[];
  /** List of supported output modalities (e.g., 'TEXT', 'IMAGE') */
  outputModalities: string[];
  /** Whether the model supports response streaming */
  responseStreamingSupported?: boolean;
  /** Maximum number of input tokens the model can accept */
  maxInputTokens: number;
  /** Maximum number of output tokens the model can generate */
  maxOutputTokens: number;
  /** Lifecycle status of the model (e.g., 'ACTIVE', 'LEGACY') */
  modelLifecycle?: string | null;
  /** AppRole IDs that have access to this model (preferred over availableToRoles) */
  allowedAppRoles: string[];
  /** @deprecated Legacy JWT role names - use allowedAppRoles instead */
  availableToRoles: string[];
  /** Whether the model is enabled for use */
  enabled: boolean;
  /** Input price per million tokens (in USD) */
  inputPricePerMillionTokens: number;
  /** Output price per million tokens (in USD) */
  outputPricePerMillionTokens: number;
  /** Cache write price per million tokens (in USD) - Bedrock only */
  cacheWritePricePerMillionTokens?: number | null;
  /** Cache read price per million tokens (in USD) - Bedrock only */
  cacheReadPricePerMillionTokens?: number | null;
  /** Whether this is a reasoning model (e.g., o1, o3) */
  isReasoningModel: boolean;
  /** Knowledge cutoff date for the model */
  knowledgeCutoffDate?: string | null;
  /** Whether this model supports prompt caching (Bedrock only) */
  supportsCaching: boolean;
  /** Whether this is the default model for new sessions */
  isDefault: boolean;
  /** Endpoint URL for OpenAI-compatible providers (Ollama, vLLM, Databricks, Azure, etc.) */
  endpointUrl?: string | null;
  /** Environment variable name holding the API key for this provider */
  apiKeyEnvVar?: string | null;
  /** Date the model was added to the system (ISO string from API) */
  createdAt?: string | Date;
  /** Date the model was last updated (ISO string from API) */
  updatedAt?: string | Date;
}

/**
 * Form data for creating or editing a managed model.
 */
export interface ManagedModelFormData {
  /** Bedrock model ID */
  modelId: string;
  /** Human-readable name of the model */
  modelName: string;
  /** Model provider (AWS, OpenAI, Google) */
  provider: ModelProvider;
  /** Provider name (e.g., 'Anthropic', 'Amazon', 'Meta') */
  providerName: string;
  /** List of supported input modalities */
  inputModalities: string[];
  /** List of supported output modalities */
  outputModalities: string[];
  /** Whether the model supports response streaming */
  responseStreamingSupported: boolean;
  /** Maximum number of input tokens the model can accept */
  maxInputTokens: number;
  /** Maximum number of output tokens the model can generate */
  maxOutputTokens: number;
  /** Lifecycle status of the model */
  modelLifecycle?: string | null;
  /** AppRole IDs that have access to this model */
  allowedAppRoles: string[];
  /** @deprecated Legacy JWT role names - use allowedAppRoles instead */
  availableToRoles: string[];
  /** Whether the model is enabled for use */
  enabled: boolean;
  /** Input price per million tokens (in USD) */
  inputPricePerMillionTokens: number;
  /** Output price per million tokens (in USD) */
  outputPricePerMillionTokens: number;
  /** Cache write price per million tokens (in USD) - Bedrock only */
  cacheWritePricePerMillionTokens?: number | null;
  /** Cache read price per million tokens (in USD) - Bedrock only */
  cacheReadPricePerMillionTokens?: number | null;
  /** Whether this is a reasoning model (e.g., o1, o3) */
  isReasoningModel: boolean;
  /** Knowledge cutoff date for the model */
  knowledgeCutoffDate?: string | null;
  /** Whether this model supports prompt caching (Bedrock only) */
  supportsCaching?: boolean;
  /** Whether this is the default model for new sessions */
  isDefault: boolean;
  /** Endpoint URL for OpenAI-compatible providers */
  endpointUrl?: string | null;
  /** Environment variable name holding the API key */
  apiKeyEnvVar?: string | null;
}

/**
 * @deprecated Use AppRoles from the /admin/roles API instead.
 * These legacy JWT roles are kept for backward compatibility only.
 */
export const AVAILABLE_ROLES = [
  'Admin',
  'SuperAdmin',
  'DotNetDevelopers',
  'User',
  'Guest',
] as const;
