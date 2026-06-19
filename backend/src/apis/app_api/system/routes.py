"""System status and first-boot API routes."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from apis.shared.database import get_database, Collections
from apis.shared.users.models import UserProfile, UserStatus

from .models import FirstBootRequest, FirstBootResponse, SystemStatusResponse
from .repository import get_system_settings_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
async def get_system_status() -> SystemStatusResponse:
    """Check if first-boot has been completed. Public — no auth required."""
    try:
        repo = get_system_settings_repository()
        settings = await repo.get_first_boot_status()
        return SystemStatusResponse(
            first_boot_completed=settings is not None and settings.get("completed") is True,
        )
    except Exception:
        logger.exception("Failed to read first-boot status")
        return SystemStatusResponse(first_boot_completed=False)


@router.post("/first-boot", status_code=200)
async def first_boot(request: FirstBootRequest) -> FirstBootResponse:
    """
    Create the initial admin user. One-time only — returns 409 if already done.

    Public endpoint (no auth required). Flow:
    1. Check first-boot status (409 if already completed)
    2. Verify email is not already taken
    3. Hash password with bcrypt
    4. Insert admin user into users collection with system_admin role
    5. Mark first-boot completed
    """
    if not _local_auth_enabled():
        raise HTTPException(
            status_code=400,
            detail="First-boot requires LOCAL_AUTH_ENABLED=true.",
        )

    settings_repo = get_system_settings_repository()

    # 1. Check if first-boot already completed
    try:
        existing = await settings_repo.get_first_boot_status()
        if existing and existing.get("completed"):
            raise HTTPException(status_code=409, detail="First-boot has already been completed.")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to check first-boot status")
        raise HTTPException(status_code=500, detail="Failed to check first-boot status.")

    db = get_database()

    # 2. Check email uniqueness
    existing_user = await db[Collections.USERS].find_one({"email": request.email.lower()})
    if existing_user:
        raise HTTPException(status_code=409, detail="A user with that email already exists.")

    # 3. Hash password
    from apis.shared.auth.local_jwt_validator import hash_password
    try:
        password_hash = hash_password(request.password)
    except Exception:
        logger.exception("Password hashing failed")
        raise HTTPException(status_code=500, detail="Failed to process password.")

    # 4. Create admin user in MongoDB
    user_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    email_domain = request.email.split("@")[1] if "@" in request.email else ""

    user_doc = {
        "_id": user_id,
        "email": request.email.lower(),
        "name": request.username,
        "roles": ["system_admin"],
        "email_domain": email_domain,
        "created_at": now_iso,
        "last_login_at": now_iso,
        "status": UserStatus.ACTIVE,
        "password_hash": password_hash,
    }

    try:
        await db[Collections.USERS].insert_one(user_doc)
    except Exception:
        logger.exception("Failed to create admin user in MongoDB")
        raise HTTPException(status_code=500, detail="Failed to create user.")

    # 5. Mark first-boot completed
    try:
        await settings_repo.mark_first_boot_completed(
            user_id=user_id,
            username=request.username,
            email=request.email.lower(),
        )
    except Exception:
        logger.exception("Failed to mark first-boot completed — rolling back user")
        await db[Collections.USERS].delete_one({"_id": user_id})
        raise HTTPException(status_code=500, detail="Failed to mark first-boot completed.")

    logger.info("First-boot completed: admin user created (id=%s)", user_id)
    return FirstBootResponse(
        success=True,
        user_id=user_id,
        message="First-boot completed. Admin user created successfully.",
    )


def _local_auth_enabled() -> bool:
    import os
    return os.environ.get("LOCAL_AUTH_ENABLED", "false").lower() == "true"
