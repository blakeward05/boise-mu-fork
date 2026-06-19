"""Local HS256 JWT validator and token issuer for dev/bootstrap auth.

Only active when LOCAL_AUTH_ENABLED=true. Tokens are signed with LOCAL_JWT_SECRET.
Never expose this in production without a strong, randomly-generated secret.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List

import jwt
from fastapi import HTTPException, status

from .models import User

logger = logging.getLogger(__name__)

LOCAL_ISSUER = "local"


class LocalJWTValidator:
    """Validates HS256 JWTs issued by this service's /auth/local/login endpoint."""

    def __init__(self, secret: str):
        self._secret = secret

    def validate_token(self, token: str) -> User:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=LOCAL_ISSUER,
                options={"verify_exp": True, "verify_aud": False},
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token expired.")
        except jwt.InvalidIssuerError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token issuer.")
        except jwt.PyJWTError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

        return User(
            user_id=payload["sub"],
            email=payload.get("email", ""),
            name=payload.get("name", ""),
            roles=payload.get("roles", []),
        )


def issue_local_token(
    user_id: str,
    email: str,
    name: str,
    roles: List[str],
    secret: str,
    expires_hours: int = 8,
) -> str:
    """Issue a signed HS256 JWT for local authentication."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "name": name,
        "roles": roles,
        "iss": LOCAL_ISSUER,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the given password."""
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False
