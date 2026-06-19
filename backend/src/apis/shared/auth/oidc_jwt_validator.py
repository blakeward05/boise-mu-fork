"""Generic OIDC JWT validator (RS256 via JWKS).

Works with Azure Entra, Google, Okta, Auth0, or any standards-compliant OIDC provider.
Configure with OIDC_ISSUER, OIDC_JWKS_URL, OIDC_AUDIENCE, and OIDC_ROLES_CLAIM.
"""

import json
import logging
from typing import List, Optional

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException, status

from .models import User

logger = logging.getLogger(__name__)


class OIDCJWTValidator:
    """Validates RS256 JWTs against a configurable OIDC JWKS endpoint."""

    def __init__(
        self,
        issuer: str,
        jwks_url: str,
        audience: Optional[str] = None,
        roles_claim: str = "roles",
    ):
        self._issuer = issuer
        self._audience = audience
        self._roles_claim = roles_claim
        self._jwks_client = PyJWKClient(jwks_url, cache_keys=True)

    def validate_token(self, token: str) -> User:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                options={"verify_exp": True, "verify_aud": False},
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token expired.")
        except jwt.InvalidIssuerError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token issuer.")
        except jwt.InvalidSignatureError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature.")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"OIDC token validation failed: {e}")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

        if self._audience:
            token_aud = payload.get("aud") or payload.get("client_id", "")
            aud_list = token_aud if isinstance(token_aud, list) else [token_aud]
            if self._audience not in aud_list:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token audience mismatch.")

        roles = self._extract_roles(payload)
        email = (payload.get("email") or payload.get("preferred_username") or "").lower()
        name = (
            payload.get("name")
            or f"{payload.get('given_name', '')} {payload.get('family_name', '')}".strip()
            or email
        )

        return User(
            user_id=payload["sub"],
            email=email,
            name=name,
            roles=roles,
            picture=payload.get("picture"),
        )

    def _extract_roles(self, payload: dict) -> List[str]:
        raw = payload.get(self._roles_claim, [])
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(r).strip() for r in parsed if str(r).strip()]
            except (json.JSONDecodeError, TypeError):
                pass
            return [r.strip() for r in raw.split(",") if r.strip()]
        return list(raw) if isinstance(raw, list) else []
