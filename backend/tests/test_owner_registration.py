"""Unit tests for user registration and team owner role assignment."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.auth.dependencies import get_current_team
from app.auth.models import User
from app.auth.permissions import ACTION_CATALOG
from app.auth.service import verify_register


def test_registered_user_is_team_owner_with_full_actions():
    """Verify newly registered user (owner_user_id=None) gets all ACTION_CATALOG actions."""
    async def _run():
        db = AsyncMock()
        owner = User(
            id=10,
            phone_number="09121112233",
            password_hash="hashed_pw",
            display_name="Mahdi",
            is_verified=True,
            owner_user_id=None,
            role_id=None,
        )

        team = await get_current_team(user=owner, db=db)
        assert team.owner == owner
        assert team.current_user == owner
        assert team.role is None
        assert team.actions == set(ACTION_CATALOG.keys())
        assert team.scope == []

    asyncio.run(_run())


def test_verify_register_sets_none_for_owner_and_role():
    """Verify verify_register assigns owner_user_id=None and role_id=None."""
    async def _run():
        db = AsyncMock()
        db.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # New user
        db.execute.return_value = mock_result

        with patch("app.auth.service._consume_otp", new_callable=AsyncMock), \
             patch("app.auth.service.log_audit", new_callable=AsyncMock), \
             patch("app.auth.service._issue_tokens", return_value={"access_token": "a", "refresh_token": "r"}):
            
            created_users = []
            db.add.side_effect = lambda obj: created_users.append(obj) if isinstance(obj, User) else None

            await verify_register(
                db=db,
                raw_phone="09121112233",
                code="123456",
                password="SecurePassword123!",
                display_name="Owner User",
                device_info="TestAgent",
            )

            assert len(created_users) == 1
            user = created_users[0]
            assert user.owner_user_id is None
            assert user.role_id is None
            assert user.is_verified is True

    asyncio.run(_run())
