"""AI orchestration service: provider resolution, tier-based routing, and generation."""

from collections.abc import AsyncIterator
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import build_system_prompt, build_user_prompt
from app.ai.providers.base import BaseAIProvider
from app.ai.providers.mock import MockAIProvider
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas import (
    AIModelInfo,
    AIModelsListResponse,
    GeneratePostRequest,
    GeneratedPostResponse,
)
from app.auth.dependencies import TeamContext
from app.core.config import get_settings
from app.subscriptions.models import Plan
from app.subscriptions.service import get_current_subscription

logger = logging.getLogger(__name__)


def get_ai_provider() -> BaseAIProvider:
    """Resolve the active AI provider based on configuration."""
    settings = get_settings()

    if settings.ai_default_provider == "openrouter" and settings.openrouter_api_key:
        return OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            site_url=settings.openrouter_site_url,
            app_name=settings.openrouter_app_name,
            timeout=settings.ai_request_timeout,
            fallback_models=[settings.ai_pro_model, settings.ai_free_model],
        )

    return MockAIProvider(provider_name="mock")


async def resolve_user_ai_tier(
    db: AsyncSession,
    team: TeamContext,
) -> tuple[str, str]:
    """Resolve the user's tier ('free', 'pro', 'enterprise') and corresponding model.

    Returns:
        (tier, model_name)
    """
    settings = get_settings()
    sub = await get_current_subscription(db, team.owner.id)

    if not sub:
        return "free", settings.ai_free_model

    plan_result = await db.execute(select(Plan).where(Plan.id == sub.plan_id))
    plan = plan_result.scalar_one_or_none()

    if not plan:
        return "free", settings.ai_free_model

    slug = (plan.slug or "").lower()
    if "enterprise" in slug or "agency" in slug:
        return "enterprise", settings.ai_enterprise_model
    elif "pro" in slug or "growth" in slug:
        return "pro", settings.ai_pro_model
    else:
        return "free", settings.ai_free_model


async def generate_post(
    db: AsyncSession,
    team: TeamContext,
    request: GeneratePostRequest,
) -> GeneratedPostResponse:
    """Generate a complete structured post tailored to the platform and user's tier."""
    provider = get_ai_provider()
    tier, model = await resolve_user_ai_tier(db, team)

    system_prompt = build_system_prompt(request)
    user_prompt = build_user_prompt(request)

    logger.info("Generating post for team owner %d using tier=%s model=%s", team.owner.id, tier, model)

    response = await provider.generate_structured(
        prompt=user_prompt,
        model=model,
        response_model=GeneratedPostResponse,
        system_prompt=system_prompt,
    )
    return response


async def stream_post(
    db: AsyncSession,
    team: TeamContext,
    request: GeneratePostRequest,
) -> AsyncIterator[str]:
    """Stream token deltas for interactive studio creation."""
    provider = get_ai_provider()
    tier, model = await resolve_user_ai_tier(db, team)

    system_prompt = build_system_prompt(request)
    user_prompt = (
        f"Topic: {request.topic}\n"
        f"Platform: {request.platform}\n"
        f"Tone: {request.tone}\n"
        "Write an engaging, high-converting post following the system prompt guidelines."
    )
    if request.extra_instructions:
        user_prompt += f"\nAdditional: {request.extra_instructions}"

    logger.info("Streaming post for team owner %d using tier=%s model=%s", team.owner.id, tier, model)

    async for token in provider.stream_text(
        prompt=user_prompt,
        model=model,
        system_prompt=system_prompt,
    ):
        yield token


async def list_available_models(
    db: AsyncSession,
    team: TeamContext,
) -> AIModelsListResponse:
    """List available models and highlight current user tier assignment."""
    settings = get_settings()
    current_tier, assigned_model = await resolve_user_ai_tier(db, team)

    models_info = [
        AIModelInfo(
            id=settings.ai_free_model,
            name="Gemini 2.0 Flash (Free)",
            tier="free",
            description="مدل پرسرعت و رایگان برای ایده‌پردازی و تولید پست‌های استاندارد",
        ),
        AIModelInfo(
            id=settings.ai_pro_model,
            name="GPT-4o Mini (Pro)",
            tier="pro",
            description="مدل هوشمند، سریع و بهینه‌شده برای کپشن‌نویسی و جذب مخاطب",
        ),
        AIModelInfo(
            id=settings.ai_enterprise_model,
            name="Claude 3.5 Sonnet (Enterprise)",
            tier="enterprise",
            description="پرچمدار نگارش هوشمند، لحن برند اختصاصی و کپی‌رایتینگ سطح بالا",
        ),
    ]

    return AIModelsListResponse(
        current_tier=current_tier,
        assigned_model=assigned_model,
        models=models_info,
    )
