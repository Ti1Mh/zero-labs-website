"""Comprehensive tests for AI Studio, OpenRouter integration, and tier-based routing."""

import asyncio
from collections.abc import AsyncIterator
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ai.prompts import build_system_prompt, build_user_prompt
from app.ai.providers.mock import MockAIProvider
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas import (
    GeneratePostRequest,
    GeneratedPostResponse,
)
from app.ai.service import (
    generate_post,
    list_available_models,
    resolve_user_ai_tier,
    stream_post,
)
from app.auth.dependencies import TeamContext
from app.auth.models import User
from app.subscriptions.models import Plan, Subscription


def test_mock_ai_provider_text_and_streaming():
    """Verify MockAIProvider text generation and SSE token streaming."""
    async def _run():
        provider = MockAIProvider()
        text = await provider.generate_text("هوش مصنوعی در بازاریابی", model="test-model")
        assert "هوش مصنوعی" in text
        assert "test-model" in text

        streamed_chunks = []
        async for chunk in provider.stream_text("تکنولوژی آینده", model="test-model"):
            streamed_chunks.append(chunk)
        assert len(streamed_chunks) > 0
        full_stream = "".join(streamed_chunks)
        assert "شبیه‌سازی‌شده" in full_stream

    asyncio.run(_run())


def test_mock_ai_provider_structured_generation():
    """Verify MockAIProvider produces schema-compliant GeneratedPostResponse."""
    async def _run():
        provider = MockAIProvider()
        res = await provider.generate_structured(
            prompt="آموزش هوش مصنوعی برای کسب و کار",
            model="mock-model",
            response_model=GeneratedPostResponse,
        )
        assert isinstance(res, GeneratedPostResponse)
        assert len(res.headline) > 0
        assert len(res.hook) > 0
        assert len(res.body) > 0
        assert len(res.hashtags) > 0
        assert 0 <= res.virality_score <= 100
        assert res.model_used == "mock-model"
        assert res.provider_used == "mock"

    asyncio.run(_run())


def test_prompt_builders():
    """Verify system and user prompt generation across platforms."""
    req_tg = GeneratePostRequest(
        topic="راهنمای راه‌اندازی استارتاپ",
        platform="telegram",
        tone="educational",
        target_audience="توسعه‌دهندگان و بنیان‌گذاران",
    )
    sys_tg = build_system_prompt(req_tg)
    assert "TELEGRAM" in sys_tg
    assert "educational" in sys_tg
    assert "توسعه‌دهندگان" in sys_tg

    user_tg = build_user_prompt(req_tg)
    assert "راهنمای راه‌اندازی استارتاپ" in user_tg
    assert "headline" in user_tg
    assert "virality_score" in user_tg

    req_ig = GeneratePostRequest(
        topic="۳ اشتباه رایج",
        platform="instagram",
        tone="witty",
        include_hashtags=False,
    )
    user_ig = build_user_prompt(req_ig)
    assert "'hashtags': []" in user_ig


def test_openrouter_provider_generate_text():
    """Verify OpenRouterProvider makes valid HTTP POST request and parses text."""
    async def _run():
        provider = OpenRouterProvider(api_key="test-key", base_url="https://test.openrouter.ai/v1")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "پاسخ متنی آزمایشی"}}]
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            output = await provider.generate_text("سلام", model="anthropic/claude-3.5-sonnet")

            assert output == "پاسخ متنی آزمایشی"
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args.kwargs
            assert "Authorization" in call_kwargs["headers"]
            assert call_kwargs["json"]["model"] == "anthropic/claude-3.5-sonnet"

    asyncio.run(_run())


def test_openrouter_provider_generate_structured():
    """Verify OpenRouterProvider requests json_object and validates Pydantic model."""
    async def _run():
        provider = OpenRouterProvider(api_key="test-key")

        mock_payload = {
            "headline": "چگونه با AI محتوا بسازیم؟",
            "hook": "راز ساخت ۱۰۰ پست در ۱۰ دقیقه 🚀",
            "body": "اینجا توضیحات کامل پست قرار می‌گیرد.",
            "hashtags": ["#هوش_مصنوعی", "#تولید_محتوا"],
            "call_to_action": "همین الان امتحان کن!",
            "virality_score": 92,
            "suggested_media_prompt": "Cinematic visual of AI studio workspace",
        }

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps(mock_payload)}}]
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            res = await provider.generate_structured(
                prompt="ایده پست هوش مصنوعی",
                model="openai/gpt-4o-mini",
                response_model=GeneratedPostResponse,
            )

            assert isinstance(res, GeneratedPostResponse)
            assert res.headline == "چگونه با AI محتوا بسازیم؟"
            assert res.virality_score == 92
            assert res.model_used == "openai/gpt-4o-mini"
            assert res.provider_used == "openrouter"

    asyncio.run(_run())


def test_tier_resolution():
    """Verify user tier and model selection based on active subscription plan."""
    async def _run():
        owner = User(id=10, phone_number="09121111111", password_hash="hash")
        team = TeamContext(owner=owner, current_user=owner, role=None, actions=set(), scope=[])
        db = AsyncMock()

        # 1) No subscription -> Free tier
        with patch("app.ai.service.get_current_subscription", new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = None
            tier, model = await resolve_user_ai_tier(db, team)
            assert tier == "free"
            assert "gemini" in model.lower() or "free" in model.lower()

        # 2) Pro subscription -> Pro tier
        sub_pro = Subscription(id=1, user_id=10, plan_id=2, status="active")
        plan_pro = Plan(id=2, name="Pro Plan", slug="pro-monthly")
        mock_res_pro = MagicMock()
        mock_res_pro.scalar_one_or_none.return_value = plan_pro
        db.execute.return_value = mock_res_pro

        with patch("app.ai.service.get_current_subscription", new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = sub_pro
            tier, model = await resolve_user_ai_tier(db, team)
            assert tier == "pro"
            assert "4o-mini" in model or "pro" in model

        # 3) Enterprise subscription -> Enterprise tier
        sub_ent = Subscription(id=2, user_id=10, plan_id=3, status="active")
        plan_ent = Plan(id=3, name="Enterprise", slug="enterprise-annual")
        mock_res_ent = MagicMock()
        mock_res_ent.scalar_one_or_none.return_value = plan_ent
        db.execute.return_value = mock_res_ent

        with patch("app.ai.service.get_current_subscription", new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = sub_ent
            tier, model = await resolve_user_ai_tier(db, team)
            assert tier == "enterprise"
            assert "claude" in model.lower() or "sonnet" in model.lower()

    asyncio.run(_run())


def test_service_generate_and_stream():
    """Verify service level generate_post, stream_post, and list_available_models."""
    async def _run():
        owner = User(id=5, phone_number="09122222222", password_hash="hash")
        team = TeamContext(owner=owner, current_user=owner, role=None, actions=set(), scope=[])
        db = AsyncMock()

        with patch("app.ai.service.get_current_subscription", new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = None  # free tier

            # Test generate_post
            req = GeneratePostRequest(topic="موفقیت در بازاریابی محتوا", platform="telegram")
            post = await generate_post(db, team, req)
            assert isinstance(post, GeneratedPostResponse)
            assert len(post.headline) > 0

            # Test stream_post
            chunks = []
            async for token in stream_post(db, team, req):
                chunks.append(token)
            assert len(chunks) > 0

            # Test list_available_models
            models_resp = await list_available_models(db, team)
            assert models_resp.current_tier == "free"
            assert len(models_resp.models) == 3

    asyncio.run(_run())


def test_ai_router_endpoints():
    """Verify FastAPI AI router integration using httpx AsyncClient."""
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

        from app.auth.dependencies import get_current_team
        from app.core.database import get_db

        mock_db = AsyncMock()

        app.dependency_overrides[get_current_team] = lambda: mock_team
        app.dependency_overrides[get_db] = lambda: mock_db

        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                with patch("app.ai.service.get_current_subscription", new_callable=AsyncMock) as mock_sub:
                    mock_sub.return_value = None

                    # 1. GET /api/v1/ai/models
                    resp_models = await client.get("/api/v1/ai/models")
                    assert resp_models.status_code == 200
                    data_models = resp_models.json()
                    assert data_models["current_tier"] == "free"
                    assert len(data_models["models"]) == 3

                    # 2. POST /api/v1/ai/generate
                    payload = {
                        "topic": "معرفی محصول جدید کفش ورزشی",
                        "platform": "instagram",
                        "tone": "engaging",
                    }
                    resp_gen = await client.post("/api/v1/ai/generate", json=payload)
                    assert resp_gen.status_code == 200
                    data_gen = resp_gen.json()
                    assert "headline" in data_gen
                    assert "hook" in data_gen
                    assert "body" in data_gen
                    assert data_gen["virality_score"] >= 0

                    # 3. POST /api/v1/ai/stream
                    resp_stream = await client.post("/api/v1/ai/stream", json=payload)
                    assert resp_stream.status_code == 200
                    assert "text/event-stream" in resp_stream.headers.get("content-type", "")
                    content = resp_stream.text
                    assert "event: chunk" in content
                    assert "event: done" in content

                    # 4. Check 403 when user lacks ai:use action
                    mock_team_no_ai = TeamContext(
                        owner=owner,
                        current_user=owner,
                        role=None,
                        actions={"content:view"},
                        scope=[],
                    )
                    app.dependency_overrides[get_current_team] = lambda: mock_team_no_ai
                    resp_forbidden = await client.post("/api/v1/ai/generate", json=payload)
                    assert resp_forbidden.status_code == 403
        finally:
            app.dependency_overrides.clear()

    asyncio.run(_run())
