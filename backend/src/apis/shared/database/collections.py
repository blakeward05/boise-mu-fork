"""MongoDB collection name constants.

Single source of truth for all collection names.
Import from here — never hardcode strings in repository classes.
"""


class Collections:
    USERS = "users"
    SESSIONS = "sessions"
    COST_RECORDS = "cost_records"
    USER_COST_SUMMARIES = "user_cost_summaries"
    SYSTEM_ROLLUPS = "system_rollups"
    QUOTA_TIERS = "quota_tiers"
    QUOTA_ASSIGNMENTS = "quota_assignments"
    QUOTA_EVENTS = "quota_events"
    MANAGED_MODELS = "managed_models"
    USER_SETTINGS = "user_settings"
    API_KEYS = "api_keys"
    APP_ROLES = "app_roles"
    AUTH_PROVIDERS = "auth_providers"
    OAUTH_PROVIDERS = "oauth_providers"
    OAUTH_USER_TOKENS = "oauth_user_tokens"
    ASSISTANTS = "assistants"
    USER_FILES = "user_files"
    SHARED_CONVERSATIONS = "shared_conversations"
    RATE_LIMIT_WINDOWS = "rate_limit_windows"
    ASSISTANT_SHARES = "assistant_shares"
