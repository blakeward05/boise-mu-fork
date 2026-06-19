"""Admin API routes for OIDC authentication provider management.

All endpoints require system admin access since authentication
configuration is a security-sensitive operation.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from apis.shared.auth import User
from apis.shared.auth_providers.models import (
    AuthProviderCreate,
    AuthProviderListResponse,
    AuthProviderResponse,
    AuthProviderUpdate,
    OIDCDiscoveryRequest,
    OIDCDiscoveryResponse,
)
from apis.shared.auth_providers.service import get_auth_provider_service
from apis.shared.rbac.system_admin import require_system_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth-providers", tags=["admin-auth-providers"])


@router.get(
    "/",
    response_model=AuthProviderListResponse,
    summary="List all authentication providers",
)
async def list_auth_providers(
    enabled_only: bool = Query(False, description="Filter to enabled providers only"),
    admin_user: User = Depends(require_system_admin),
) -> AuthProviderListResponse:
    """List all configured OIDC authentication providers."""
    logger.info("Admin listing auth providers")

    service = get_auth_provider_service()
    providers = await service.list_providers(enabled_only=enabled_only)

    return AuthProviderListResponse(
        providers=[AuthProviderResponse.from_provider(p) for p in providers],
        total=len(providers),
    )


@router.get(
    "/runtime-image-tag",
    summary="Get current runtime container image tag",
)
async def get_runtime_image_tag(
    admin_user: User = Depends(require_system_admin),
) -> dict:
    """Return the runtime image tag from the RUNTIME_IMAGE_TAG environment variable."""
    import os
    image_tag = os.environ.get("RUNTIME_IMAGE_TAG", "latest")
    return {"image_tag": image_tag}


@router.post(
    "/discover",
    response_model=OIDCDiscoveryResponse,
    summary="Discover OIDC endpoints",
)
async def discover_oidc_endpoints(
    request: OIDCDiscoveryRequest,
    admin_user: User = Depends(require_system_admin),
) -> OIDCDiscoveryResponse:
    """
    Discover OIDC endpoints from an issuer URL.

    Fetches the .well-known/openid-configuration document and returns
    the discovered endpoints, supported scopes, and claims.
    """
    logger.info("Admin discovering OIDC endpoints")

    service = get_auth_provider_service()
    return await service.discover_endpoints(request.issuer_url)


@router.get(
    "/{provider_id}",
    response_model=AuthProviderResponse,
    summary="Get authentication provider",
)
async def get_auth_provider(
    provider_id: str,
    admin_user: User = Depends(require_system_admin),
) -> AuthProviderResponse:
    """Get a specific authentication provider by ID."""
    logger.info("Admin requesting auth provider")

    service = get_auth_provider_service()
    provider = await service.get_provider(provider_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Auth provider '{provider_id}' not found",
        )

    return AuthProviderResponse.from_provider(provider)


@router.post(
    "/",
    response_model=AuthProviderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create authentication provider",
)
async def create_auth_provider(
    data: AuthProviderCreate,
    admin_user: User = Depends(require_system_admin),
) -> AuthProviderResponse:
    """
    Create a new OIDC authentication provider.

    If endpoints are not provided, they will be auto-discovered from
    the issuer URL's .well-known/openid-configuration endpoint.
    """
    logger.info("Admin creating auth provider")

    try:
        service = get_auth_provider_service()
        provider = await service.create_provider(data, created_by=admin_user.email)
        return AuthProviderResponse.from_provider(provider)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.patch(
    "/{provider_id}",
    response_model=AuthProviderResponse,
    summary="Update authentication provider",
)
async def update_auth_provider(
    provider_id: str,
    updates: AuthProviderUpdate,
    admin_user: User = Depends(require_system_admin),
) -> AuthProviderResponse:
    """
    Update an authentication provider.

    Only provided fields are updated. If issuer_url is changed,
    endpoints are re-discovered automatically.
    """
    logger.info("Admin updating auth provider")

    try:
        service = get_auth_provider_service()
        provider = await service.update_provider(provider_id, updates)

        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Auth provider '{provider_id}' not found",
            )

        return AuthProviderResponse.from_provider(provider)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete authentication provider",
)
async def delete_auth_provider(
    provider_id: str,
    admin_user: User = Depends(require_system_admin),
) -> None:
    """Delete an authentication provider and its client secret."""
    logger.info("Admin deleting auth provider")

    service = get_auth_provider_service()
    deleted = await service.delete_provider(provider_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Auth provider '{provider_id}' not found",
        )


@router.post(
    "/{provider_id}/test",
    summary="Test authentication provider connectivity",
)
async def test_auth_provider(
    provider_id: str,
    admin_user: User = Depends(require_system_admin),
) -> dict:
    """
    Test provider connectivity by verifying JWKS, discovery, and
    token endpoints are reachable.
    """
    logger.info("Admin testing auth provider")

    service = get_auth_provider_service()
    return await service.test_provider(provider_id)
