"""MongoDB repository for user management."""

import logging
from typing import Optional, List, Tuple
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from apis.shared.database import get_database, BaseRepository, Collections
from .models import UserProfile, UserListItem, UserStatus

logger = logging.getLogger(__name__)

_repository: Optional["UserRepository"] = None


def get_user_repository() -> "UserRepository":
    global _repository
    if _repository is None:
        _repository = UserRepository()
    return _repository


class UserRepository(BaseRepository):
    """MongoDB repository for user operations.

    Document schema:
        _id:          user_id (string)
        email:        lowercase email
        name:         display name
        roles:        list of JWT role strings
        email_domain: lowercase domain portion of email
        created_at:   ISO timestamp
        last_login_at: ISO timestamp
        status:       "active" | "suspended" | "deleted"
        picture:      optional URL
    """

    def __init__(self) -> None:
        super().__init__(get_database(), Collections.USERS)

    # ── Single user ────────────────────────────────────────────────

    async def get_user(self, user_id: str) -> Optional[UserProfile]:
        doc = await self._find_one({"_id": user_id})
        return self._doc_to_profile(doc) if doc else None

    async def get_user_by_user_id(self, user_id: str) -> Optional[UserProfile]:
        """Alias kept for call-site compatibility."""
        return await self.get_user(user_id)

    async def get_user_by_email(self, email: str) -> Optional[UserProfile]:
        doc = await self._find_one({"email": email.lower()})
        return self._doc_to_profile(doc) if doc else None

    async def create_user(self, profile: UserProfile) -> UserProfile:
        doc = self._profile_to_doc(profile)
        try:
            await self._insert_one(doc)
        except DuplicateKeyError:
            raise ValueError(f"User {profile.user_id} already exists")
        logger.info("Created user: %s (%s)", profile.user_id, profile.email)
        return profile

    async def update_user(self, profile: UserProfile) -> UserProfile:
        status_value = (
            profile.status.value
            if isinstance(profile.status, UserStatus)
            else profile.status
        )
        # Use $set so password_hash and other fields not in UserProfile are preserved
        await self._collection.update_one(
            {"_id": profile.user_id},
            {"$set": {
                "email": profile.email.lower(),
                "name": profile.name,
                "roles": profile.roles,
                "email_domain": profile.email_domain.lower(),
                "last_login_at": profile.last_login_at,
                "status": status_value,
                "picture": profile.picture,
            }},
            upsert=False,
        )
        return profile

    async def upsert_user(self, profile: UserProfile) -> Tuple[UserProfile, bool]:
        existing = await self.get_user(profile.user_id)
        if existing:
            profile.created_at = existing.created_at
            await self.update_user(profile)
            return profile, False
        await self.create_user(profile)
        return profile, True

    # ── List operations ────────────────────────────────────────────

    async def list_users_by_domain(
        self,
        domain: str,
        limit: int = 25,
        last_evaluated_key: Optional[dict] = None,
    ) -> Tuple[List[UserListItem], Optional[dict]]:
        skip = int(last_evaluated_key.get("skip", 0)) if last_evaluated_key else 0
        docs = await self._find_many(
            {"email_domain": domain.lower()},
            sort=[("last_login_at", DESCENDING)],
            limit=limit,
            skip=skip,
        )
        items = [self._doc_to_list_item(d) for d in docs]
        next_key = {"skip": skip + limit} if len(docs) == limit else None
        return items, next_key

    async def list_users_by_status(
        self,
        status: str = "active",
        limit: int = 25,
        last_evaluated_key: Optional[dict] = None,
    ) -> Tuple[List[UserListItem], Optional[dict]]:
        skip = int(last_evaluated_key.get("skip", 0)) if last_evaluated_key else 0
        docs = await self._find_many(
            {"status": status},
            sort=[("last_login_at", DESCENDING)],
            limit=limit,
            skip=skip,
        )
        items = [self._doc_to_list_item(d) for d in docs]
        next_key = {"skip": skip + limit} if len(docs) == limit else None
        return items, next_key

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _profile_to_doc(profile: UserProfile) -> dict:
        status_value = (
            profile.status.value
            if isinstance(profile.status, UserStatus)
            else profile.status
        )
        return {
            "_id": profile.user_id,
            "email": profile.email.lower(),
            "name": profile.name,
            "roles": profile.roles,
            "email_domain": profile.email_domain.lower(),
            "created_at": profile.created_at,
            "last_login_at": profile.last_login_at,
            "status": status_value,
            "picture": profile.picture,
        }

    @staticmethod
    def _doc_to_profile(doc: dict) -> UserProfile:
        return UserProfile(
            user_id=doc["_id"],
            email=doc["email"],
            name=doc.get("name", ""),
            roles=doc.get("roles", []),
            picture=doc.get("picture"),
            email_domain=doc.get("email_domain", ""),
            created_at=doc.get("created_at", ""),
            last_login_at=doc.get("last_login_at", doc.get("created_at", "")),
            status=doc.get("status", "active"),
        )

    @staticmethod
    def _doc_to_list_item(doc: dict) -> UserListItem:
        return UserListItem(
            user_id=doc["_id"],
            email=doc["email"],
            name=doc.get("name", ""),
            status=doc.get("status", "active"),
            last_login_at=doc.get("last_login_at", doc.get("created_at", "")),
            email_domain=doc.get("email_domain"),
        )
