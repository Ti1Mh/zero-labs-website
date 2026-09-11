"""Comprehensive tests for Brand Persona, Content Doctor, Smart Scheduling, and Repurposing."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ai.models import BrandPersona
from app.ai.prompts import (
    build_doctor_prompts,
    build_repurpose_prompts,
    build_system_prompt,
)
from app.ai.schemas import (
    CreateBrandPersonaRequest,
    GeneratePostRequest,
    OptimizePostRequest,
    OptimizePostResponse,
    RepurposeRequest,
    RepurposeResponse,
    SmartScheduleRequest,
    ToneTraitsSchema,
    UpdateBrandPersonaRequest,
)
from app.ai.service import (
    create_brand_persona,
    delete_brand_persona,
    generate_post,
    get_brand_persona,
    get_team_published_winners,
    list_brand_personas,
    optimize_post,
    recommend_smart_schedule,
    repurpose_post,
    set_default_brand_persona,
    update_brand_persona,
)
from app.auth.dependencies import TeamContext
from app.auth.models import User
from app.core.exceptions import NotFoundError


def test_persona_crud_service_methods():
    """Verify Brand Persona creation, listing, updating, and default toggling."""
    async def _run():
        owner = User(id=1, phone_number="09121111111", password_hash="hash")
        team = TeamContext(owner=owner, current_user=owner, role=None, actions={"ai:use"}, scope=[])
        db = AsyncMock()

        # 1. Create persona
        create_req = CreateBrandPersonaRequest(
            name="مزون لوکس مانلی",
            description="برند پوشاک زنانه لوکس",
            tone_traits=ToneTraitsSchema(formality=4, humor=1, enthusiasm=5, boldness=4),
            forbidden_words=["ارزان", "حراج", "دم‌دستی"],
            signature_phrases=["تجربه اصالت و شکوه"],
            sample_posts=["پست نمونه ۱: پیراهن ساتن مجلسی با دوخت دست‌دوز"],
            target_audience="بانوان خوش‌پوش ۲۵ تا ۴۵ سال",
            is_default=True,
        )

        persona = await create_brand_persona(db, team, create_req)
        assert persona.name == "مزون لوکس مانلی"
        assert persona.owner_id == 1
        assert persona.is_default is True
        assert "ارزان" in persona.forbidden_words
        db.add.assert_called_once()
        db.flush.assert_called()

        # 2. Get persona
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = persona
        db.execute.return_value = mock_result

        fetched = await get_brand_persona(db, team, persona_id=1)
        assert fetched.name == "مزون لوکس مانلی"

        # 3. Update persona
        update_req = UpdateBrandPersonaRequest(
            name="مزون لوکس مانلی (جدید)",
            forbidden_words=["ارزان", "حراجی", "رایگان"],
        )
        updated = await update_brand_persona(db, team, persona_id=1, request=update_req)
        assert updated.name == "مزون لوکس مانلی (جدید)"
        assert "رایگان" in updated.forbidden_words

        # 4. Set default
        with_default = await set_default_brand_persona(db, team, persona_id=1)
        assert with_default.is_default is True

        # 5. Delete persona
        await delete_brand_persona(db, team, persona_id=1)
        db.delete.assert_called_with(persona)

    asyncio.run(_run())


def test_persona_and_analytics_prompt_injection():
    """Verify Brand Persona constraints and past winners are properly rendered in system prompt."""
    persona = BrandPersona(
        id=1,
        owner_id=1,
        name="آکادمی کدنویسی",
        description="آموزش برنامه‌نویسی برای متخصصین",
        tone_traits={"formality": 2, "humor": 4, "enthusiasm": 5, "boldness": 3},
        forbidden_words=["ساده‌ترین راه پولدار شدن", "یک‌شبه برنامه‌نویس شو"],
        signature_phrases=["کد تمیز، زندگی آرام"],
        sample_posts=["چگونه با ریفکتورینگ ۵۰٪ سرعت کد را بالا بردیم؟"],
        target_audience="توسعه‌دهندگان پایتون و ری‌اکت",
        is_default=True,
    )
    past_winners = [
        "پست برنده هفته گذشته: ۱۰ نکته داکر برای توسعه‌دهندگان بک‌اند",
    ]

    req = GeneratePostRequest(
        topic="نکات پیشرفته معماری نرم‌افزار",
        platform="telegram",
        tone="educational",
    )

    sys_prompt = build_system_prompt(req, persona=persona, past_winners=past_winners)

    # Verify persona injection
    assert "آکادمی کدنویسی" in sys_prompt
    assert "ساده‌ترین راه پولدار شدن" in sys_prompt
    assert "کد تمیز، زندگی آرام" in sys_prompt
    assert "چگونه با ریفکتورینگ" in sys_prompt
    # Verify analytics injection
    assert "۱۰ نکته داکر" in sys_prompt


def test_content_doctor_optimization():
    """Verify Content Doctor audits, scores, and rewrites a draft post."""
    async def _run():
        owner = User(id=1, phone_number="09121111111", password_hash="hash")
        team = TeamContext(owner=owner, current_user=owner, role=None, actions={"ai:use"}, scope=[])
        db = AsyncMock()

        # Mock no persona in DB
        mock_persona_res = MagicMock()
        mock_persona_res.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_persona_res

        with patch("app.ai.service.get_current_subscription", new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = None

            req = OptimizePostRequest(
                draft_text="ما امروز تخفیف داریم برای محصولاتمون بیایید بخرید.",
                platform="instagram",
            )
            res = await optimize_post(db, team, req)

            assert isinstance(res, OptimizePostResponse)
            assert 0 <= res.overall_score <= 100
            assert 0 <= res.hook_score <= 100
            assert len(res.strengths) > 0
            assert len(res.weaknesses) > 0
            assert len(res.improved_version) > 0
            assert len(res.alternative_hooks) == 3

    asyncio.run(_run())


def test_smart_scheduling_recommendation():
    """Verify Smart Schedule analyzes timeline points and returns reasoned slots."""
    async def _run():
        owner = User(id=1, phone_number="09121111111", password_hash="hash")
        team = TeamContext(owner=owner, current_user=owner, role=None, actions={"ai:use"}, scope=[])
        db = AsyncMock()

        with patch("app.ai.service.get_timeline", new_callable=AsyncMock) as mock_timeline:
            mock_timeline.return_value = [
                {"date": "2026-09-01", "published": 5, "failed": 0, "cancelled": 0, "queued": 0},
                {"date": "2026-09-02", "published": 3, "failed": 1, "cancelled": 0, "queued": 0},
            ]

            req = SmartScheduleRequest(platform="telegram")
            res = await recommend_smart_schedule(db, team, req)

            assert res.platform == "telegram"
            assert len(res.recommended_slots) >= 2
            assert res.recommended_slots[0].confidence_score >= 80
            assert "۱۹:۳۰" in res.recommended_slots[0].reasoning or "تعامل" in res.recommended_slots[0].reasoning
            assert "۸ پست موفق" in res.analytics_insight or "موفق" in res.analytics_insight

    asyncio.run(_run())


def test_repurpose_content_cascade():
    """Verify Repurposing adapts master content across multiple platforms."""
    async def _run():
        owner = User(id=1, phone_number="09121111111", password_hash="hash")
        team = TeamContext(owner=owner, current_user=owner, role=None, actions={"ai:use"}, scope=[])
        db = AsyncMock()

        mock_persona_res = MagicMock()
        mock_persona_res.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_persona_res

        with patch("app.ai.service.get_current_subscription", new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = None

            req = RepurposeRequest(
                source_text="در این مقاله توضیح می‌دهیم چرا استمرار در شبکه‌های اجتماعی عامل شماره ۱ موفقیت است.",
                target_platforms=["telegram", "instagram", "twitter"],
            )
            res = await repurpose_post(db, team, req)

            assert isinstance(res, RepurposeResponse)
            assert len(res.source_summary) > 0
            assert "telegram" in res.posts
            assert "instagram" in res.posts
            assert "twitter" in res.posts
            assert len(res.posts["telegram"].headline) > 0

    asyncio.run(_run())


def test_ai_advanced_router_endpoints():
    """Verify HTTP integration for Personas, Content Doctor, Smart Schedule, and Repurpose."""
    from app.auth.dependencies import get_current_team
    from app.core.database import get_db
    from app.main import app

    async def _run():
        owner = User(id=1, phone_number="09123456789", password_hash="pwd", owner_user_id=None)
        mock_team = TeamContext(
            owner=owner,
            current_user=owner,
            role=None,
            actions={"ai:use"},
            scope=[],
        )
        mock_db = AsyncMock()
        mock_exec_res = MagicMock()
        mock_exec_res.scalar_one_or_none.return_value = None
        mock_exec_res.all.return_value = []
        mock_db.execute.return_value = mock_exec_res

        app.dependency_overrides[get_current_team] = lambda: mock_team
        app.dependency_overrides[get_db] = lambda: mock_db

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                with patch("app.ai.service.get_current_subscription", new_callable=AsyncMock) as mock_sub:
                    mock_sub.return_value = None

                    # 1. POST /api/v1/ai/personas (Create Persona)
                    persona_payload = {
                        "name": "تست برند",
                        "description": "توضیحات تست",
                        "tone_traits": {"formality": 3, "humor": 2, "enthusiasm": 4, "boldness": 3},
                        "forbidden_words": ["ارزان"],
                        "signature_phrases": ["کیفیت برتر"],
                        "sample_posts": ["پست تست"],
                        "target_audience": "عموم",
                        "is_default": True,
                    }
                    mock_persona_obj = BrandPersona(
                        id=10,
                        owner_id=1,
                        name="تست برند",
                        description="توضیحات تست",
                        tone_traits={"formality": 3, "humor": 2, "enthusiasm": 4, "boldness": 3},
                        forbidden_words=["ارزان"],
                        signature_phrases=["کیفیت برتر"],
                        sample_posts=["پست تست"],
                        target_audience="عموم",
                        is_default=True,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )

                    with patch("app.ai.router.create_brand_persona", new_callable=AsyncMock) as mock_create:
                        mock_create.return_value = mock_persona_obj
                        resp_persona = await client.post("/api/v1/ai/personas", json=persona_payload)
                        assert resp_persona.status_code == 201
                        data_p = resp_persona.json()
                        assert data_p["name"] == "تست برند"
                        assert data_p["id"] == 10

                    # 2. GET /api/v1/ai/personas (List Personas)
                    with patch("app.ai.router.list_brand_personas", new_callable=AsyncMock) as mock_list:
                        mock_list.return_value = [mock_persona_obj]
                        resp_list = await client.get("/api/v1/ai/personas")
                        assert resp_list.status_code == 200
                        assert len(resp_list.json()) == 1

                    # 3. POST /api/v1/ai/optimize (Content Doctor)
                    doc_payload = {
                        "draft_text": "یک متن تستی برای بهینه‌سازی و افزایش نمره تعامل",
                        "platform": "telegram",
                    }
                    resp_doc = await client.post("/api/v1/ai/optimize", json=doc_payload)
                    assert resp_doc.status_code == 200
                    data_doc = resp_doc.json()
                    assert "overall_score" in data_doc
                    assert "improved_version" in data_doc

                    # 4. POST /api/v1/ai/repurpose
                    repurpose_payload = {
                        "source_text": "محتوای اصلی جهت تبدیل به چندین پلتفرم به صورت همزمان",
                        "target_platforms": ["telegram", "instagram"],
                    }
                    resp_rep = await client.post("/api/v1/ai/repurpose", json=repurpose_payload)
                    assert resp_rep.status_code == 200
                    data_rep = resp_rep.json()
                    assert "source_summary" in data_rep
                    assert "posts" in data_rep

                    # 5. POST /api/v1/ai/smart-schedule
                    with patch("app.ai.service.get_timeline", new_callable=AsyncMock) as mock_timeline:
                        mock_timeline.return_value = []
                        sched_payload = {"platform": "instagram"}
                        resp_sched = await client.post("/api/v1/ai/smart-schedule", json=sched_payload)
                        assert resp_sched.status_code == 200
                        data_sched = resp_sched.json()
                        assert len(data_sched["recommended_slots"]) >= 1
        finally:
            app.dependency_overrides.clear()

    asyncio.run(_run())
