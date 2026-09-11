"""Unit tests for subscription quota enforcement."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.dependencies import TeamContext
from app.auth.models import User
from app.core.exceptions import AuthorizationError
from app.subscriptions.dependencies import require_active_subscription
from app.subscriptions.models import Plan, Subscription


def test_quota_raises_when_no_active_subscription():
    """Verify AuthorizationError is raised when user has no active subscription."""
    async def _run():
        team = TeamContext(
            owner=User(id=1, phone_number="09123456789", password_hash="x"),
            current_user=User(id=1, phone_number="09123456789", password_hash="x"),
            role=None,
            actions=set(),
            scope=[],
        )
        db = AsyncMock()

        with patch("app.subscriptions.dependencies.get_current_subscription", new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = None
            with pytest.raises(AuthorizationError) as exc_info:
                await require_active_subscription(team=team, db=db)
            assert "نیاز به اشتراک فعال دارید" in str(exc_info.value)

    asyncio.run(_run())


def test_quota_raises_when_limit_reached():
    """Verify AuthorizationError is raised when monthly quota is reached."""
    async def _run():
        team = TeamContext(
            owner=User(id=1, phone_number="09123456789", password_hash="x"),
            current_user=User(id=1, phone_number="09123456789", password_hash="x"),
            role=None,
            actions=set(),
            scope=[],
        )
        sub = Subscription(id=1, user_id=1, plan_id=1, status="active")
        plan = Plan(id=1, name="Free", slug="free", monthly_quota=10)

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = plan
        db.execute.return_value = mock_result

        with patch("app.subscriptions.dependencies.get_current_subscription", new_callable=AsyncMock) as mock_sub, \
             patch("app.subscriptions.dependencies.get_monthly_usage", new_callable=AsyncMock) as mock_usage:
            mock_sub.return_value = sub
            mock_usage.return_value = 10

            with pytest.raises(AuthorizationError) as exc_info:
                await require_active_subscription(team=team, db=db)
            assert "سهمیه ماهانه شما" in str(exc_info.value)

    asyncio.run(_run())


def test_quota_passes_when_under_limit():
    """Verify check passes when monthly usage is strictly below monthly_quota."""
    async def _run():
        team = TeamContext(
            owner=User(id=1, phone_number="09123456789", password_hash="x"),
            current_user=User(id=1, phone_number="09123456789", password_hash="x"),
            role=None,
            actions=set(),
            scope=[],
        )
        sub = Subscription(id=1, user_id=1, plan_id=1, status="active")
        plan = Plan(id=1, name="Free", slug="free", monthly_quota=10)

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = plan
        db.execute.return_value = mock_result

        with patch("app.subscriptions.dependencies.get_current_subscription", new_callable=AsyncMock) as mock_sub, \
             patch("app.subscriptions.dependencies.get_monthly_usage", new_callable=AsyncMock) as mock_usage:
            mock_sub.return_value = sub
            mock_usage.return_value = 9

            result_team = await require_active_subscription(team=team, db=db)
            assert result_team == team

    asyncio.run(_run())


def test_quota_passes_when_unlimited():
    """Verify check passes when monthly_quota == 0 (unlimited)."""
    async def _run():
        team = TeamContext(
            owner=User(id=1, phone_number="09123456789", password_hash="x"),
            current_user=User(id=1, phone_number="09123456789", password_hash="x"),
            role=None,
            actions=set(),
            scope=[],
        )
        sub = Subscription(id=1, user_id=1, plan_id=1, status="active")
        plan = Plan(id=1, name="Business", slug="business", monthly_quota=0)

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = plan
        db.execute.return_value = mock_result

        with patch("app.subscriptions.dependencies.get_current_subscription", new_callable=AsyncMock) as mock_sub, \
             patch("app.subscriptions.dependencies.get_monthly_usage", new_callable=AsyncMock) as mock_usage:
            mock_sub.return_value = sub
            mock_usage.return_value = 99999

            result_team = await require_active_subscription(team=team, db=db)
            assert result_team == team

    asyncio.run(_run())
