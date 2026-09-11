"""FastAPI dependencies for protected endpoints."""

import jwt as pyjwt
from dataclasses import dataclass
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User
from app.auth.permissions import ACTION_CATALOG
from app.auth.security import decode_access_token
from app.core.database import get_db
from app.core.exceptions import AuthenticationError, AuthorizationError

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """Resolve the authenticated user from a Bearer access token."""
    if credentials is None:
        raise AuthenticationError("احراز هویت لازم است.")
    try:
        user_id = decode_access_token(credentials.credentials)
    except pyjwt.InvalidTokenError as exc:
        raise AuthenticationError("توکن معتبر نیست.") from exc
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("کاربر معتبر نیست.")
    return user


async def require_superuser(
    user: User = Depends(get_current_user),
) -> User:
    """Ensure the authenticated user is a platform superuser."""
    if not user.is_superuser:
        raise AuthorizationError("دسترسی به این بخش نیازمند سطح دسترسی مدیر کل است.")
    return user


@dataclass
class TeamContext:
    """The team context for the current request."""

    owner: User  # The team owner (self if owner, else the owner_user)
    current_user: User  # The authenticated user (owner or member)
    role: Role | None  # The role of the current user (None if owner)
    actions: set[str]  # Allowed actions for the current user
    scope: list[dict]  # Channel/platform scope items


async def get_current_team(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TeamContext:
    """Resolve the team context for the current user."""
    if user.owner_user_id is None:
        # User is the team owner
        return TeamContext(
            owner=user,
            current_user=user,
            role=None,
            actions=set(ACTION_CATALOG.keys()),  # Owner has all actions
            scope=[],  # Owner has unlimited scope
        )
    # User is a team member
    result = await db.execute(select(Role).where(Role.id == user.role_id))
    role = result.scalar_one_or_none()
    if role is None:
        raise AuthenticationError("نقش کاربر یافت نشد.")
    owner_result = await db.execute(select(User).where(User.id == user.owner_user_id))
    owner = owner_result.scalar_one()
    return TeamContext(
        owner=owner,
        current_user=user,
        role=role,
        actions=set(role.actions),
        scope=role.scope,
    )


def require(action: str):
    """Dependency that enforces the user has the given action."""
    async def _check(team: TeamContext = Depends(get_current_team)) -> TeamContext:
        if action not in team.actions:
            raise AuthorizationError(f"دسترسی لازم برای {action} ندارید.")
        return team
    return _check