"""Tests for the /system/first-boot and /system/status endpoints."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


def _make_settings_repo(completed: bool = False) -> MagicMock:
    repo = MagicMock()
    status_doc = {"completed": True} if completed else None
    repo.get_first_boot_status = AsyncMock(return_value=status_doc)
    repo.mark_first_boot_completed = AsyncMock(return_value=None)
    return repo


def _make_db(existing_user: dict | None = None) -> MagicMock:
    db = MagicMock()
    col = MagicMock()
    col.find_one = AsyncMock(return_value=existing_user)
    col.insert_one = AsyncMock(return_value=None)
    col.delete_one = AsyncMock(return_value=None)
    db.__getitem__ = MagicMock(return_value=col)
    return db


async def _call_first_boot(
    settings_repo,
    db,
    username="admin",
    email="admin@example.com",
    password="Str0ng!Pass1",
    local_auth_enabled=True,
):
    from apis.app_api.system.models import FirstBootRequest
    from apis.app_api.system.routes import first_boot

    request = FirstBootRequest(username=username, email=email, password=password)

    env = {"LOCAL_AUTH_ENABLED": "true" if local_auth_enabled else "false"}
    with patch("apis.app_api.system.routes.get_system_settings_repository", return_value=settings_repo), \
         patch("apis.app_api.system.routes.get_database", return_value=db), \
         patch.dict("os.environ", env):
        return await first_boot(request)


class TestFirstBootEndpoint:

    async def test_requires_local_auth_enabled(self):
        repo = _make_settings_repo(completed=False)
        db = _make_db()
        with pytest.raises(HTTPException) as exc:
            await _call_first_boot(repo, db, local_auth_enabled=False)
        assert exc.value.status_code == 400

    async def test_rejects_when_already_completed(self):
        repo = _make_settings_repo(completed=True)
        db = _make_db()
        with pytest.raises(HTTPException) as exc:
            await _call_first_boot(repo, db)
        assert exc.value.status_code == 409

    async def test_rejects_duplicate_email(self):
        repo = _make_settings_repo(completed=False)
        db = _make_db(existing_user={"_id": "existing-id", "email": "admin@example.com"})
        with pytest.raises(HTTPException) as exc:
            await _call_first_boot(repo, db)
        assert exc.value.status_code == 409

    async def test_successful_first_boot(self):
        repo = _make_settings_repo(completed=False)
        db = _make_db()

        result = await _call_first_boot(repo, db)

        assert result.success is True
        assert result.user_id  # non-empty UUID
        repo.mark_first_boot_completed.assert_called_once()
        call_kwargs = repo.mark_first_boot_completed.call_args.kwargs
        assert call_kwargs["username"] == "admin"
        assert call_kwargs["email"] == "admin@example.com"

    async def test_user_inserted_with_system_admin_role(self):
        repo = _make_settings_repo(completed=False)
        db = _make_db()
        users_col = db["users"]

        await _call_first_boot(repo, db, username="myadmin", email="me@corp.io")

        users_col.insert_one.assert_called_once()
        inserted = users_col.insert_one.call_args.args[0]
        assert inserted["email"] == "me@corp.io"
        assert inserted["name"] == "myadmin"
        assert "system_admin" in inserted["roles"]
        assert inserted["email_domain"] == "corp.io"
        assert "password_hash" in inserted

    async def test_rollback_on_mark_completed_failure(self):
        repo = _make_settings_repo(completed=False)
        repo.mark_first_boot_completed = AsyncMock(side_effect=RuntimeError("write failed"))
        db = _make_db()
        users_col = db["users"]

        with pytest.raises(HTTPException) as exc:
            await _call_first_boot(repo, db)
        assert exc.value.status_code == 500
        users_col.delete_one.assert_called_once()


class TestSystemStatus:

    async def test_returns_false_when_not_completed(self):
        from apis.app_api.system.routes import get_system_status

        repo = _make_settings_repo(completed=False)
        with patch("apis.app_api.system.routes.get_system_settings_repository", return_value=repo):
            result = await get_system_status()
        assert result.first_boot_completed is False

    async def test_returns_true_when_completed(self):
        from apis.app_api.system.routes import get_system_status

        repo = _make_settings_repo(completed=True)
        with patch("apis.app_api.system.routes.get_system_settings_repository", return_value=repo):
            result = await get_system_status()
        assert result.first_boot_completed is True
