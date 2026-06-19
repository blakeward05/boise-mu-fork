# Local Migration Plan

Migrates the app from AWS-native (Bedrock AgentCore, DynamoDB, Cognito, S3) to a local-first stack that can be deployed to Azure with minimal changes.

## Design Principles

- **Local-first, cloud-ready** — every abstraction is designed so swapping an env var or implementation class moves it to Azure
- **MongoDB everywhere** — local MongoDB → Azure Cosmos DB for MongoDB (wire-protocol compatible, connection string only)
- **Open interfaces** — `FileStorage`, `VectorStore`, `EmbeddingModel`, `SessionManager` are abstract; implementations are swapped per environment
- **No AWS required to run** — all AWS SDK calls are behind feature flags or removed

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                  FRONTEND (Angular 21, port 4200)               │
│  Auth: MSAL (Azure Entra) + local login form fallback           │
└────────────────────┬────────────────────────────────────────────┘
                     │ Bearer JWT
        ┌────────────┴───────────────┐
        │                            │
┌───────▼──────────────┐   ┌────────▼──────────────────┐
│  APP API (port 8000) │   │ INFERENCE API (port 8001)  │
│  FastAPI + admin     │   │ FastAPI + SSE streaming     │
└───────────────────────┘   └───────────────────────────┘
        │                            │
        └────────────┬───────────────┘
                     │
            ┌────────▼────────┐
            │    MongoDB      │  ← Cosmos DB for MongoDB (cloud)
            │  (18 collections)│
            └─────────────────┘

FILE STORAGE: Local filesystem  →  Azure Blob Storage
VECTOR STORE: Chroma (local)    →  Azure AI Search
AUTH PROVIDER: Azure Entra OIDC →  Azure Entra (same, different tenant config)
LLM PROVIDERS: configurable     →  same + Bedrock option
```

---

## LLM Providers

All providers are configured in the `managed_models` MongoDB collection via the admin panel. No hardcoded endpoints.

| Provider | Auth | Notes |
|----------|------|-------|
| OpenAI-compatible | Base URL + API key | Covers Ollama, LM Studio, vLLM, LocalAI |
| AWS Bedrock | IAM / AWS credentials | Optional; keep for cloud parity |
| Databricks | Workspace URL + token | OpenAI-compatible endpoint |
| Azure AI Foundry | Endpoint + API key | OpenAI-compatible with Azure auth |
| Azure APIM | Endpoint + subscription key | Gateway-managed access |

---

## Authentication

| Mode | Flow | When to use |
|------|------|-------------|
| Azure Entra SSO | OIDC/PKCE via MSAL | Primary; all production usage |
| Local username/password | POST `/auth/local/login` → JWT | Bootstrap; dev/testing |

Backend JWT validation is provider-agnostic — configured via `OIDC_ISSUER` and `OIDC_JWKS_URL`. Both Azure Entra and local-issued tokens go through the same validator.

---

## Environment Variables

### Old → New mapping

```bash
# REMOVED — replaced by a single DATABASE_URL
DYNAMODB_MANAGED_MODELS_TABLE_NAME
DYNAMODB_SESSIONS_METADATA_TABLE_NAME
DYNAMODB_COST_SUMMARY_TABLE_NAME
DYNAMODB_SYSTEM_ROLLUP_TABLE_NAME
DYNAMODB_OIDC_STATE_TABLE_NAME
DYNAMODB_QUOTA_TABLE
DYNAMODB_QUOTA_EVENTS_TABLE
DYNAMODB_USERS_TABLE_NAME
DYNAMODB_APP_ROLES_TABLE_NAME
DYNAMODB_USER_FILES_TABLE_NAME
DYNAMODB_OAUTH_PROVIDERS_TABLE_NAME
DYNAMODB_OAUTH_USER_TOKENS_TABLE_NAME
DYNAMODB_AUTH_PROVIDERS_TABLE_NAME
DYNAMODB_ASSISTANTS_TABLE_NAME
DYNAMODB_USER_SETTINGS_TABLE_NAME
SHARED_CONVERSATIONS_TABLE_NAME
AGENTCORE_MEMORY_ID               # Session history now in MongoDB

# NEW
DATABASE_URL=mongodb://localhost:27017      # → Cosmos DB connection string in Azure
DATABASE_NAME=boise
LOCAL_STORAGE_PATH=./data/files            # → Azure Blob Storage (swap FileStorage impl)
CHROMA_PERSIST_DIR=./data/chroma           # → Azure AI Search (swap VectorStore impl)
OIDC_ISSUER=https://login.microsoftonline.com/{tenant}/v2.0
OIDC_JWKS_URL=https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys
OIDC_AUDIENCE={azure-app-client-id}
LOCAL_AUTH_ENABLED=true                    # Set false in production
LOCAL_JWT_SECRET=change-me-in-production
```

---

## MongoDB Collections

18 collections replacing 17 DynamoDB tables (single-table design unpacked into proper collections):

| Collection | Purpose | Key indexes |
|------------|---------|-------------|
| `users` | User profiles | `email` (unique), `email_domain+last_login_at`, `status+last_login_at` |
| `sessions` | Chat sessions | `user_id+status+last_message_at`, `session_id` (unique) |
| `cost_records` | Per-message cost/token data | `user_id+timestamp`, `session_id+timestamp`, TTL |
| `user_cost_summaries` | Pre-aggregated cost by user/period | `user_id+period` (unique) |
| `system_rollups` | System-wide daily/monthly/model rollups | `rollup_type+date` (unique) |
| `quota_tiers` | Quota tier definitions | `tier_id` (unique) |
| `quota_assignments` | User quota assignments | `user_id` (unique), `role_name` |
| `quota_events` | Quota block/warning audit log | `user_id+timestamp`, TTL |
| `managed_models` | LLM model + provider configurations | `model_id` (unique), `provider` |
| `user_settings` | Per-user preferences | `user_id` (unique) |
| `api_keys` | API key storage | `key_hash` (unique), `user_id` |
| `app_roles` | RBAC role definitions | `role_name` (unique) |
| `auth_providers` | OIDC provider configurations | `provider_id` (unique) |
| `oauth_providers` | OAuth provider configs (Google, Azure, etc.) | `provider_name` (unique) |
| `oauth_user_tokens` | Stored user OAuth tokens | `user_id+provider_name` (unique) |
| `assistants` | Assistant metadata | `assistant_id` (unique), `owner_id`, `visibility` |
| `user_files` | File metadata and ownership | `file_id` (unique), `user_id` |
| `shared_conversations` | Shared conversation snapshots | `share_id` (unique), `session_id` |

---

## Key Interfaces

Every cloud service is hidden behind an interface. To deploy to Azure, swap the implementation — not the calling code.

```python
# Storage
class FileStorage(ABC):
    async def upload(self, key: str, data: bytes, content_type: str) -> str: ...
    async def download(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def get_url(self, key: str, expires_in: int = 3600) -> str: ...

# Vector store (RAG)
class VectorStore(ABC):
    async def store_chunks(self, assistant_id, document_id, chunks, embeddings, metadata) -> None: ...
    async def search(self, assistant_id, query_embedding, top_k=5) -> List[Dict]: ...
    async def delete_by_document(self, document_id) -> int: ...

# Embeddings
class EmbeddingModel(ABC):
    async def embed(self, texts: List[str]) -> List[List[float]]: ...

# Session history
class SessionManager(ABC):
    def initialize(self, agent) -> None: ...
    def append_message(self, message, agent) -> None: ...
    async def update_after_turn(self, input_tokens: int) -> None: ...
```

---

## Cloud Migration Checklist

When moving to Azure, change **only** these things:

| What | Local value | Azure value |
|------|-------------|-------------|
| `DATABASE_URL` | `mongodb://localhost:27017` | Cosmos DB connection string |
| `FileStorage` impl | `LocalFileStorage` | `AzureBlobStorage` |
| `VectorStore` impl | `ChromaVectorStore` | `AzureAISearchVectorStore` |
| `OIDC_ISSUER` | local JWT secret | Azure Entra tenant URL |
| Container runtime | Docker Compose | Azure Container Apps or AKS |
| CI/CD | local | GitHub Actions → Azure |

### Cosmos DB Compatibility Rules
These constraints are enforced from day one so there are no surprises at migration time:

1. MongoDB 4.0 API features only — no 6.x/7.x-only operators
2. No `$lookup` in hot paths — expensive in Cosmos DB (RU per join)
3. TTL indexes defined explicitly — Cosmos DB supports them, same syntax
4. `_id` is the primary identifier everywhere
5. Documents stay under 2MB
6. No `$where` or server-side JavaScript

---

## Implementation Status

| Phase | Description | Status |
|-------|-------------|--------|
| **Phase 0** | MongoDB connection layer + Docker Compose + env config | ✅ Complete |
| **Phase 1** | MongoDB repository implementations (18 collections) | ✅ Complete |
| **Phase 2** | Auth: OIDC JWT generalization + local auth + MSAL frontend | ✅ Complete |
| **Phase 3** | MongoDB session manager (replacing AgentCore Memory) | ✅ Complete |
| **Phase 4** | LLM provider extensions (OpenAI-compat, Databricks, Azure AI Foundry, APIM) | ✅ Complete |
| **Phase 5** | File storage abstraction (local filesystem → Azure Blob) | ✅ Complete |
| **Phase 6** | RAG with Chroma (replace S3 Vectors + Lambda ingestion) | ✅ Complete |
| **Phase 7** | Admin panel: provider management + user bootstrap | ✅ Complete |

---

## Local Development Setup

```bash
# 1. Start the full stack
docker compose up -d

# 2. Backend (app_api)
cd backend
uv sync --extra agentcore --extra dev
cd src/apis/app_api && uv run python main.py

# 3. Backend (inference_api)
cd backend/src/apis/inference_api && uv run python main.py

# 4. Frontend
cd frontend/ai.client
npm install && npm run start
```

Copy `backend/src/.env.local.example` to `backend/src/.env` and fill in your values before starting.

---

## File Locations

| New file | Purpose |
|----------|---------|
| `backend/src/apis/shared/database/connection.py` | MongoDB Motor client factory |
| `backend/src/apis/shared/database/base_repository.py` | Abstract base repository |
| `backend/src/apis/shared/database/indexes.py` | All collection index definitions |
| `backend/src/apis/shared/database/collections.py` | Collection name constants |
| `backend/src/apis/shared/storage/file_storage.py` | FileStorage abstract interface |
| `backend/src/apis/shared/storage/local_file_storage.py` | Local filesystem implementation |
| `backend/src/apis/shared/vector_store/base.py` | VectorStore + EmbeddingModel interfaces |
| `backend/src/apis/shared/vector_store/chroma_store.py` | Chroma implementation |
| `backend/src/agents/main_agent/session/mongo_session_manager.py` | MongoDB session manager |
| `docker-compose.yml` | Full local dev stack |
| `backend/src/.env.local.example` | Local-first env template |
