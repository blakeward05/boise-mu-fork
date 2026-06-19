"""Local username/password authentication endpoint.

Only active when LOCAL_AUTH_ENABLED=true. Used for first-run bootstrap
and development. In production, disable this and use OIDC SSO.
"""

import logging
import os

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/local", tags=["auth"])


class LocalLoginRequest(BaseModel):
    email: EmailStr
    password: str


class LocalLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _local_auth_enabled() -> bool:
    return os.environ.get("LOCAL_AUTH_ENABLED", "false").lower() == "true"


def _get_secret() -> str:
    secret = os.environ.get("LOCAL_JWT_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local auth secret is not configured.",
        )
    return secret


@router.post("/login", response_model=LocalLoginResponse, summary="Local username/password login")
async def local_login(request: LocalLoginRequest) -> LocalLoginResponse:
    """
    Authenticate with email + password.

    Returns a short-lived JWT (8 h) that is accepted by all endpoints
    via the same Bearer token mechanism used for OIDC tokens.

    Only available when LOCAL_AUTH_ENABLED=true.
    """
    if not _local_auth_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local auth is not enabled.")

    secret = _get_secret()

    from apis.shared.database import get_database, Collections
    from apis.shared.auth.local_jwt_validator import verify_password, issue_local_token

    db = get_database()
    doc = await db[Collections.USERS].find_one(
        {"email": request.email.lower()},
        {"_id": 1, "email": 1, "name": 1, "roles": 1, "password_hash": 1, "status": 1},
    )

    if not doc or not doc.get("password_hash"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    if doc.get("status") == "inactive":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive.")

    if not verify_password(request.password, doc["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    token = issue_local_token(
        user_id=str(doc["_id"]),
        email=doc["email"],
        name=doc.get("name", ""),
        roles=doc.get("roles", []),
        secret=secret,
    )

    return LocalLoginResponse(access_token=token)
