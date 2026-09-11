"""Comprehensive tests for Super Admin backoffice, Plan CRUD, AI Usage Ledger, Safety Guard, and Retry."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.admin.schemas import (
    AdminPlanCreate,
    AdminPlanUpdate,
    UpdateUserStatusRequest,
)
from app.admin.service import (
    create_plan_admin,
    get_ai_ledger_summary,
    get_plan_admin,
    list_plans_admin,
    list_users_admin,
    toggle_plan_status_admin,
    update_plan_admin,
    update_user_status_admin,
)
from app.ai.ledger_models import AIUsageLedger
from app.ai.safety import check_content_safety
from app.ai.schemas import GeneratePostRequest, GeneratedPostResponse
from app.ai.service import _execute_with_retry, calculate_cost_cents, generate_post, record_ai_usage
from app.auth.dependencies import TeamContext, get_current_user, require_superuser
from app.auth.models import User
from app.core.database import get_db
from app.core.exceptions import AuthorizationError, ConflictError, InvalidInputError, NotFoundError
from app.main import app
from app.subscriptions.models import Plan


def test_content_safety_guardrail():
    """Verify pre-publish regex blocklist catches prohibited keywords and scams."""
    safe_text = "آموزش سئو و بهینه‌سازی محتوا برای اینستاگرام"
    is_safe, reason = check_content_safety(safe_text)
    assert is_safe is True
    assert reason is None

    unsafe_scam = "لطفاً شماره کارت و رمز خود را ارسال نمایید"
    is_safe, reason = check_content_safety(unsafe_scam)
    assert is_safe is False
    assert "عبارت غیرمجاز" in reason

    unsafe_gambling = "بهترین سایت قمار و شرط‌بندی آنلاین با بانس ویژه"
    is_safe, reason = check_content_safety(unsafe_gambling)
    assert is_safe is False

    unsafe_profanity = "This is a scam and total shit"
    is_safe, reason = check_content_safety(unsafe_profanity)
    assert is_safe is False


def test_calculate_cost_cents():
    """Verify tier cost calculations for token usage."""
    # Free model has zero API cost
    assert calculate_cost_cents("google/gemini-2.0-flash-exp:free", 1000, 2000) == 0

    # Claude 3.5 Sonnet: $3/M prompt, $15/M completion
    claude_cost = calculate_cost_cents("anthropic/claude-3.5-sonnet", 10000, 5000)
    assert claude_cost > 0

    # GPT-4o-mini
    gpt_cost = calculate_cost_cents("openai/gpt-4o-mini", 10000, 5000)
    assert gpt_cost > 0


def test_execute_with_retry_fallback():
    """Verify 1-retry fallback executes when first AI provider attempt fails."""
    async def _run():
        mock_provider = MagicMock()
        mock_success_response = GeneratedPostResponse(
            headline="تست موفق",
            hook="هوک جذاب",
            body="متن عالی",
            hashtags=["#تست"],
            call_to_action="کلیک کنید",
            virality_score=90,
            suggested_media_prompt=None,
            model_used="mock",
            provider_used="mock",
        )

        # Fail on attempt 1 with InvalidInputError, succeed on attempt 2
        mock_provider.generate_structured = AsyncMock(
            side_effect=[InvalidInputError("Malformed JSON"), mock_success_response]
        )

        result = await _execute_with_retry(
            provider=mock_provider,
            prompt="موضوع تستی",
            model="mock",
            response_model=GeneratedPostResponse,
            system_prompt="دستورالعمل",
        )

        assert result == mock_success_response
        assert mock_provider.generate_structured.call_count == 2

    asyncio.run(_run())


def test_admin_plan_crud_service():
    """Verify Super Admin can create, read, update, toggle and validate duplicate plans."""
    async def _run():
        db = AsyncMock()

        # 1. Create plan
        req = AdminPlanCreate(
            name="پلن رشد پیشرفته",
            slug="growth-pro",
            description="مناسب برای کسب‌وکارهای در حال رشد",
            prices={"IRR": {"monthly": 890000, "yearly": 8900000}, "USD": {"monthly": 29, "yearly": 290}},
            monthly_quota=100,
            features={"ai_credits": 200, "accounts": 5},
            is_active=True,
        )

        # Mock query checking if plan exists
        db.execute.return_value.scalar_one_or_none.return_value = None

        plan = await create_plan_admin(db, req)
        assert plan.name == "پلن رشد پیشرفته"
        assert plan.slug == "growth-pro"
        assert plan.prices["IRR"]["monthly"] == 890000

        # 2. Duplicate slug error
        db.execute.return_value.scalar_one_or_none.return_value = plan
        with pytest.raises(ConflictError):
            await create_plan_admin(db, req)

        # 3. Update plan
        update_req = AdminPlanUpdate(
            monthly_quota=150,
            prices={"IRR": {"monthly": 990000, "yearly": 9900000}},
        )
        updated_plan = await update_plan_admin(db, plan_id=1, req=update_req)
        assert updated_plan.monthly_quota == 150
        assert updated_plan.prices["IRR"]["monthly"] == 990000

        # 4. Toggle status
        toggled = await toggle_plan_status_admin(db, plan_id=1, is_active=False)
        assert toggled.is_active is False

    asyncio.run(_run())


def test_admin_user_status_service():
    """Verify Super Admin can list users and update user permissions."""
    async def _run():
        db = AsyncMock()
        user = User(
            id=10,
            phone_number="09120000010",
            email="user@test.com",
            display_name="User 10",
            password_hash="hash",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        db.execute.return_value.scalar_one_or_none.return_value = user

        # Promote to superuser
        req = UpdateUserStatusRequest(is_superuser=True)
        updated = await update_user_status_admin(db, user_id=10, req=req)
        assert updated.is_superuser is True

        # Deactivate user
        req_deactivate = UpdateUserStatusRequest(is_active=False)
        updated_deactive = await update_user_status_admin(db, user_id=10, req=req_deactivate)
        assert updated_deactive.is_active is False

    asyncio.run(_run())


def test_ai_ledger_summary_service():
    """Verify AI usage ledger aggregations and query results."""
    async def _run():
        db = AsyncMock()

        entry1 = AIUsageLedger(
            id=1,
            owner_id=1,
            user_id=1,
            provider="openrouter",
            model="openai/gpt-4o-mini",
            tokens_prompt=500,
            tokens_completion=300,
            cost_cents=2,
            operation_type="generate",
            created_at=datetime.now(timezone.utc),
        )
        entry2 = AIUsageLedger(
            id=2,
            owner_id=1,
            user_id=1,
            provider="openrouter",
            model="anthropic/claude-3.5-sonnet",
            tokens_prompt=1200,
            tokens_completion=800,
            cost_cents=15,
            operation_type="optimize",
            created_at=datetime.now(timezone.utc),
        )

        db.execute.return_value.scalars.return_value.all.return_value = [entry2, entry1]
        db.execute.return_value.one_or_none.return_value = (2, 1700, 1100, 17)

        summary = await get_ai_ledger_summary(db, user_id=1)
        assert summary.total_invocations == 2
        assert summary.total_tokens_prompt == 1700
        assert summary.total_tokens_completion == 1100
        assert summary.total_cost_cents == 17
        assert len(summary.items) == 2

    asyncio.run(_run())


def test_admin_api_endpoints_rbac_and_crud():
    """Verify Super Admin endpoints enforce require_superuser and perform plan CRUD."""
    async def _run():
        regular_user = User(
            id=1,
            phone_number="09121111111",
            password_hash="hash",
            is_active=True,
            is_superuser=False,
        )
        superuser = User(
            id=99,
            phone_number="09129999999",
            password_hash="hash",
            is_active=True,
            is_superuser=True,
        )

        mock_db = AsyncMock()

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. Accessing as regular user -> 403 Forbidden
                app.dependency_overrides[get_current_user] = lambda: regular_user
                app.dependency_overrides[require_superuser] = lambda: (_ for _ in ()).throw(
                    AuthorizationError("دسترسی به این بخش نیازمند سطح دسترسی مدیر کل است.")
                )
                app.dependency_overrides[get_db] = lambda: mock_db

                resp_forbidden = await client.get("/api/v1/admin/plans")
                assert resp_forbidden.status_code == 403

                # 2. Accessing as superuser -> 200 OK
                app.dependency_overrides[get_current_user] = lambda: superuser
                app.dependency_overrides[require_superuser] = lambda: superuser

                existing_plan = Plan(
                    id=1,
                    name="پلن پایه",
                    slug="starter",
                    description="رایگان",
                    prices={"IRR": {"monthly": 0, "yearly": 0}},
                    monthly_quota=10,
                    features={"ai_credits": 10},
                    is_active=True,
                    created_at=datetime.now(timezone.utc),
                )
                mock_db.execute.return_value.scalars.return_value.all.return_value = [existing_plan]
                mock_db.execute.return_value.scalar_one_or_none.return_value = None

                resp_plans = await client.get("/api/v1/admin/plans")
                assert resp_plans.status_code == 200
                assert len(resp_plans.json()) == 1
                assert resp_plans.json()[0]["slug"] == "starter"

                # 3. Create Plan via POST
                new_plan_payload = {
                    "name": "پلن سازمانی",
                    "slug": "enterprise",
                    "description": "برای آژانس‌ها",
                    "prices": {"USD": {"monthly": 99, "yearly": 990}},
                    "monthly_quota": 500,
                    "features": {"ai_credits": 1000},
                    "is_active": True,
                }
                resp_create = await client.post("/api/v1/admin/plans", json=new_plan_payload)
                assert resp_create.status_code == 201
                data = resp_create.json()
                assert data["slug"] == "enterprise"

                # 4. Patch Plan
                mock_db.execute.return_value.scalar_one_or_none.return_value = existing_plan
                resp_patch = await client.patch(
                    "/api/v1/admin/plans/1",
                    json={"monthly_quota": 25},
                )
                assert resp_patch.status_code == 200

                # 5. GET /api/v1/admin/ai-ledger
                mock_db.execute.return_value.scalars.return_value.all.return_value = []
                mock_db.execute.return_value.one_or_none.return_value = (0, 0, 0, 0)
                resp_ledger = await client.get("/api/v1/admin/ai-ledger")
                assert resp_ledger.status_code == 200
                assert resp_ledger.json()["total_invocations"] == 0
        finally:
            app.dependency_overrides.clear()

    asyncio.run(_run())
