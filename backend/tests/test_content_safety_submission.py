"""Tests verifying content safety check on job submission endpoint POST /content."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import TeamContext, get_current_team
from app.auth.models import User
from app.main import app
from app.subscriptions.dependencies import require_active_subscription


@pytest.mark.anyio
async def test_create_content_job_rejects_unsafe_content():
    """Verify POST /content rejects descriptions containing hazardous phrases (e.g. gambling, drugs)."""
    user = User(id=1, phone_number="+989121111111", is_verified=True, is_active=True)
    team = TeamContext(
        owner=user,
        current_user=user,
        role=None,
        actions={"content:create"},
        scope=[],
    )

    app.dependency_overrides[get_current_team] = lambda: team
    app.dependency_overrides[require_active_subscription] = lambda: team

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Unsafe content containing gambling keyword
            res = await client.post(
                "/api/v1/content",
                json={
                    "platform_code": "telegram",
                    "description": "برای شروع بازی و برد میلیونی در کازینو آنلاین کلیک کنید!",
                },
            )
            assert res.status_code == 400
            assert "محتوا حاوی عبارت غیرمجاز یا پرخطر است" in res.json()["detail"]

            # 2. Unsafe content containing scam phrase
            res_scam = await client.post(
                "/api/v1/content",
                json={
                    "platform_code": "telegram",
                    "description": "برای دریافت جایزه شماره کارت و رمز دوم پویا را بفرستید",
                },
            )
            assert res_scam.status_code == 400
            assert "محتوا حاوی عبارت غیرمجاز یا پرخطر است" in res_scam.json()["detail"]
    finally:
        app.dependency_overrides.clear()
