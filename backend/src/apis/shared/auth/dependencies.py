"""FastAPI dependencies for authentication.

Validator selection priority:
  1. OIDC (Azure Entra, etc.) — when OIDC_ISSUER + OIDC_JWKS_URL are set  (RS256)
  2. Local HS256              — when LOCAL_AUTH_ENABLED=true + LOCAL_JWT_SECRET (HS256)
  3. Cognito fallback         — when COGNITO_USER_POOL_ID + COGNITO_APP_CLIENT_ID are set (RS256)

Tokens are routed to the matching validator by inspecting the JWT header `alg` field.
"""

import asyncio
import logging
import os
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .models import User

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# ─── Validator registry ────────────────────────────────────────────────────────
# Keyed by JWT `alg` header value.  RS256 covers both OIDC and Cognito.
# HS256 covers local development tokens.

_validators: dict = {}
_validators_ready = False


def _init_validators() -> None:
    global _validators, _validators_ready
    if _validators_ready:
        return

    # 1. Generic OIDC (Azure Entra, Okta, Auth0, …)
    oidc_issuer = os.environ.get("OIDC_ISSUER")
    jwks_url = os.environ.get("OIDC_JWKS_URL")
    if oidc_issuer and jwks_url:
        from .oidc_jwt_validator import OIDCJWTValidator
        _validators["RS256"] = OIDCJWTValidator(
            issuer=oidc_issuer,
            jwks_url=jwks_url,
            audience=os.environ.get("OIDC_AUDIENCE"),
            roles_claim=os.environ.get("OIDC_ROLES_CLAIM", "roles"),
        )
        logger.info("OIDCJWTValidator initialised (issuer=%s)", oidc_issuer)
    elif oidc_issuer:
        logger.warning("OIDC_ISSUER is set but OIDC_JWKS_URL is missing — OIDC auth disabled")

    # 2. Local HS256 (dev / bootstrap)
    local_enabled = os.environ.get("LOCAL_AUTH_ENABLED", "false").lower() == "true"
    local_secret = os.environ.get("LOCAL_JWT_SECRET")
    if local_enabled and local_secret:
        from .local_jwt_validator import LocalJWTValidator
        _validators["HS256"] = LocalJWTValidator(secret=local_secret)
        logger.info("LocalJWTValidator initialised (HS256)")
    elif local_enabled:
        logger.warning("LOCAL_AUTH_ENABLED=true but LOCAL_JWT_SECRET is not set — local auth disabled")

    # 3. Cognito fallback (RS256) — only if no RS256 validator yet
    if "RS256" not in _validators:
        user_pool_id = os.environ.get("COGNITO_USER_POOL_ID")
        client_id = os.environ.get("COGNITO_APP_CLIENT_ID")
        region = os.environ.get("COGNITO_REGION") or os.environ.get("AWS_REGION", "us-west-2")
        if user_pool_id and client_id:
            from .cognito_jwt_validator import CognitoJWTValidator
            _validators["RS256"] = CognitoJWTValidator(user_pool_id, client_id, region)
            logger.info("CognitoJWTValidator initialised (fallback)")

    if not _validators:
        logger.error(
            "No JWT validator configured. Set OIDC_ISSUER+OIDC_JWKS_URL, "
            "LOCAL_AUTH_ENABLED+LOCAL_JWT_SECRET, or Cognito env vars."
        )

    _validators_ready = True


def _pick_validator(token: str):
    _init_validators()
    try:
        alg = jwt.get_unverified_header(token).get("alg", "RS256")
    except Exception:
        alg = "RS256"
    return _validators.get(alg) or _validators.get("RS256")


# ─── User profile cache ────────────────────────────────────────────────────────

_user_profile_cache: dict[str, tuple[float, dict]] = {}
_USER_PROFILE_CACHE_TTL = 300  # 5 minutes


def invalidate_user_profile_cache(user_id: str) -> None:
    _user_profile_cache.pop(user_id, None)


_user_repository = None


def _get_user_repository():
    global _user_repository
    if _user_repository is not None:
        return _user_repository
    try:
        from apis.shared.users.repository import UserRepository
        _user_repository = UserRepository()
    except Exception as e:
        logger.warning(f"Failed to initialise UserRepository for profile cache: {e}")
    return _user_repository


async def _enrich_user_from_store(user: User) -> None:
    """Fill in missing identity claims from the users MongoDB collection."""
    import time

    now = time.monotonic()
    cached = _user_profile_cache.get(user.user_id)
    if cached:
        ts, profile = cached
        if now - ts < _USER_PROFILE_CACHE_TTL:
            user.email = profile.get("email") or user.email
            user.name = profile.get("name") or user.name
            stored_roles = profile.get("roles")
            if stored_roles:
                user.roles = stored_roles
            return

    repo = _get_user_repository()
    if not repo:
        return

    try:
        stored = await repo.get_user_by_user_id(user.user_id)
        if stored:
            profile = {"email": stored.email, "name": stored.name, "roles": stored.roles}
            _user_profile_cache[user.user_id] = (now, profile)
            user.email = stored.email or user.email
            user.name = stored.name or user.name
            if stored.roles:
                user.roles = stored.roles
    except Exception as e:
        logger.debug(f"Profile enrichment failed for {user.user_id}: {e}")


_user_sync_service = None


def _get_user_sync_service():
    global _user_sync_service
    if _user_sync_service is None:
        try:
            from apis.shared.users.repository import UserRepository
            from apis.shared.users.sync import UserSyncService
            _user_sync_service = UserSyncService(repository=UserRepository())
        except Exception as e:
            logger.warning(f"Failed to initialise UserSyncService: {e}")
    return _user_sync_service


async def _sync_user_background(sync_service, user: User) -> None:
    try:
        await sync_service.sync_user_from_jwt(user)
    except Exception as e:
        logger.warning(f"Failed to sync user {user.user_id}: {e}")


# ─── FastAPI dependencies ──────────────────────────────────────────────────────


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    validator = _pick_validator(token)

    if not validator:
        logger.error("No JWT validator available for incoming token")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service not configured.",
        )

    try:
        user = validator.validate_token(token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token validation failed: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed.")

    user.raw_token = token
    await _enrich_user_from_store(user)

    sync_service = _get_user_sync_service()
    if sync_service:
        try:
            if sync_service.enabled:
                asyncio.create_task(_sync_user_background(sync_service, user))
        except AttributeError:
            asyncio.create_task(_sync_user_background(sync_service, user))

    return user


async def get_current_user_id(user: User = Depends(get_current_user)) -> str:
    return user.user_id


async def get_current_user_trusted(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> User:
    """
    Extract user from a pre-validated JWT (no signature check).

    Use only where the network layer (e.g. AgentCore Runtime JWT authorizer)
    has already verified the token.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.DecodeError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token.")

    email = payload.get("email") or payload.get("preferred_username") or ""
    name = (
        payload.get("name")
        or f"{payload.get('given_name', '')} {payload.get('family_name', '')}".strip()
        or payload.get("cognito:username")
        or email
    )
    user_id = payload.get("sub")
    roles = payload.get("roles") or payload.get("cognito:groups") or []
    if isinstance(roles, str):
        roles = [roles]

    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user.")

    user = User(
        email=email.lower() if email else "",
        name=name,
        user_id=str(user_id),
        roles=roles,
        picture=payload.get("picture"),
        raw_token=token,
    )

    await _enrich_user_from_store(user)

    sync_service = _get_user_sync_service()
    if sync_service:
        try:
            if sync_service.enabled:
                asyncio.create_task(_sync_user_background(sync_service, user))
        except AttributeError:
            asyncio.create_task(_sync_user_background(sync_service, user))

    return user
