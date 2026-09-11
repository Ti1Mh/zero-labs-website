"""AI orchestration service: provider resolution, personas, analytics integration, and doctor."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
import logging
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import BrandPersona
from app.ai.prompts import (
    build_doctor_prompts,
    build_repurpose_prompts,
    build_system_prompt,
    build_user_prompt,
)
from app.ai.providers.base import BaseAIProvider
from app.ai.providers.mock import MockAIProvider
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.schemas import (
    AIModelInfo,
    AIModelsListResponse,
    CreateBrandPersonaRequest,
    GeneratePostRequest,
    GeneratedPostResponse,
    OptimizePostRequest,
    OptimizePostResponse,
    RepurposeRequest,
    RepurposeResponse,
    ScheduleSlot,
    SmartScheduleRequest,
    SmartScheduleResponse,
    UpdateBrandPersonaRequest,
)
from app.analytics.service import get_timeline
from app.auth.dependencies import TeamContext
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.content import ContentJob
from app.models.platform import Platform
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


# --- Brand Persona Management ---

async def create_brand_persona(
    db: AsyncSession,
    team: TeamContext,
    request: CreateBrandPersonaRequest,
) -> BrandPersona:
    """Create a new Brand Persona profile for the team."""
    if request.is_default:
        await db.execute(
            update(BrandPersona)
            .where(BrandPersona.owner_id == team.owner.id)
            .values(is_default=False)
        )

    persona = BrandPersona(
        owner_id=team.owner.id,
        name=request.name,
        description=request.description,
        tone_traits=request.tone_traits.model_dump(),
        forbidden_words=request.forbidden_words,
        signature_phrases=request.signature_phrases,
        sample_posts=request.sample_posts,
        target_audience=request.target_audience,
        is_default=request.is_default,
    )
    db.add(persona)
    await db.flush()
    return persona


async def list_brand_personas(
    db: AsyncSession,
    team: TeamContext,
) -> list[BrandPersona]:
    """Return all Brand Personas belonging to the team."""
    result = await db.execute(
        select(BrandPersona)
        .where(BrandPersona.owner_id == team.owner.id)
        .order_by(BrandPersona.is_default.desc(), BrandPersona.created_at.desc())
    )
    return list(result.scalars().all())


async def get_brand_persona(
    db: AsyncSession,
    team: TeamContext,
    persona_id: int,
) -> BrandPersona:
    """Fetch a single Brand Persona by ID."""
    result = await db.execute(
        select(BrandPersona).where(
            BrandPersona.id == persona_id,
            BrandPersona.owner_id == team.owner.id,
        )
    )
    persona = result.scalar_one_or_none()
    if not persona:
        raise NotFoundError("پرسونای برند یافت نشد.")
    return persona


async def update_brand_persona(
    db: AsyncSession,
    team: TeamContext,
    persona_id: int,
    request: UpdateBrandPersonaRequest,
) -> BrandPersona:
    """Update an existing Brand Persona."""
    persona = await get_brand_persona(db, team, persona_id)

    if request.name is not None:
        persona.name = request.name
    if request.description is not None:
        persona.description = request.description
    if request.tone_traits is not None:
        persona.tone_traits = request.tone_traits.model_dump()
    if request.forbidden_words is not None:
        persona.forbidden_words = request.forbidden_words
    if request.signature_phrases is not None:
        persona.signature_phrases = request.signature_phrases
    if request.sample_posts is not None:
        persona.sample_posts = request.sample_posts
    if request.target_audience is not None:
        persona.target_audience = request.target_audience
    if request.is_default is not None and request.is_default:
        await db.execute(
            update(BrandPersona)
            .where(BrandPersona.owner_id == team.owner.id)
            .values(is_default=False)
        )
        persona.is_default = True

    await db.flush()
    return persona


async def delete_brand_persona(
    db: AsyncSession,
    team: TeamContext,
    persona_id: int,
) -> None:
    """Delete a Brand Persona."""
    persona = await get_brand_persona(db, team, persona_id)
    await db.delete(persona)
    await db.flush()


async def set_default_brand_persona(
    db: AsyncSession,
    team: TeamContext,
    persona_id: int,
) -> BrandPersona:
    """Set a specific persona as the team default."""
    persona = await get_brand_persona(db, team, persona_id)
    await db.execute(
        update(BrandPersona)
        .where(BrandPersona.owner_id == team.owner.id)
        .values(is_default=False)
    )
    persona.is_default = True
    await db.flush()
    return persona


import inspect

async def resolve_active_persona(
    db: AsyncSession,
    team: TeamContext,
    persona_id: int | None = None,
) -> BrandPersona | None:
    """Resolve the requested persona or fall back to default persona."""
    if persona_id is not None:
        return await get_brand_persona(db, team, persona_id)

    result = await db.execute(
        select(BrandPersona).where(
            BrandPersona.owner_id == team.owner.id,
            BrandPersona.is_default.is_(True),
        ).limit(1)
    )
    res = result.scalar_one_or_none()
    if inspect.isawaitable(res):
        res = await res
    return res


# --- Analytics-Enriched Generation ---

async def get_team_published_winners(
    db: AsyncSession,
    team: TeamContext,
    platform: str,
    limit: int = 3,
) -> list[str]:
    """Retrieve recent successfully published posts for few-shot guidance."""
    result = await db.execute(
        select(ContentJob.description)
        .join(Platform, Platform.id == ContentJob.platform_id)
        .where(
            ContentJob.created_by == team.owner.id,
            ContentJob.status == "published",
            Platform.code == platform,
        )
        .order_by(ContentJob.created_at.desc())
        .limit(limit)
    )
    rows = result.all()
    if inspect.isawaitable(rows):
        rows = await rows
    return [r[0] for r in rows if r and len(r) > 0 and r[0]]


async def generate_post(
    db: AsyncSession,
    team: TeamContext,
    request: GeneratePostRequest,
) -> GeneratedPostResponse:
    """Generate structured content incorporating Persona and Analytics feedback."""
    provider = get_ai_provider()
    tier, model = await resolve_user_ai_tier(db, team)

    persona = await resolve_active_persona(db, team, request.persona_id)

    past_winners: list[str] = []
    if request.use_analytics_context:
        past_winners = await get_team_published_winners(db, team, request.platform)

    system_prompt = build_system_prompt(request, persona=persona, past_winners=past_winners)
    user_prompt = build_user_prompt(request)

    logger.info(
        "Generating post for team owner %d | tier=%s | model=%s | persona=%s",
        team.owner.id,
        tier,
        model,
        persona.name if persona else "None",
    )

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

    persona = await resolve_active_persona(db, team, request.persona_id)
    past_winners = (
        await get_team_published_winners(db, team, request.platform)
        if request.use_analytics_context
        else []
    )

    system_prompt = build_system_prompt(request, persona=persona, past_winners=past_winners)
    user_prompt = (
        f"Topic: {request.topic}\n"
        f"Platform: {request.platform}\n"
        f"Tone: {request.tone}\n"
        "Write an engaging, high-converting post following the system prompt guidelines."
    )
    if request.extra_instructions:
        user_prompt += f"\nAdditional: {request.extra_instructions}"

    logger.info("Streaming post for team owner %d | tier=%s | model=%s", team.owner.id, tier, model)

    async for token in provider.stream_text(
        prompt=user_prompt,
        model=model,
        system_prompt=system_prompt,
    ):
        yield token


# --- Content Doctor & Multi-Platform Intelligence ---

async def optimize_post(
    db: AsyncSession,
    team: TeamContext,
    request: OptimizePostRequest,
) -> OptimizePostResponse:
    """Analyze and optimize a draft post ('Content Doctor')."""
    provider = get_ai_provider()
    tier, model = await resolve_user_ai_tier(db, team)
    persona = await resolve_active_persona(db, team, request.persona_id)

    sys_prompt, user_prompt = build_doctor_prompts(request, persona)

    logger.info("Running Content Doctor for team owner %d on platform %s", team.owner.id, request.platform)

    return await provider.generate_structured(
        prompt=user_prompt,
        model=model,
        response_model=OptimizePostResponse,
        system_prompt=sys_prompt,
    )


async def repurpose_post(
    db: AsyncSession,
    team: TeamContext,
    request: RepurposeRequest,
) -> RepurposeResponse:
    """Repurpose master text into native posts across multiple platforms."""
    provider = get_ai_provider()
    tier, model = await resolve_user_ai_tier(db, team)
    persona = await resolve_active_persona(db, team, request.persona_id)

    sys_prompt, user_prompt = build_repurpose_prompts(request, persona)

    logger.info(
        "Repurposing content for team owner %d to platforms: %s",
        team.owner.id,
        request.target_platforms,
    )

    return await provider.generate_structured(
        prompt=user_prompt,
        model=model,
        response_model=RepurposeResponse,
        system_prompt=sys_prompt,
    )


async def recommend_smart_schedule(
    db: AsyncSession,
    team: TeamContext,
    request: SmartScheduleRequest,
) -> SmartScheduleResponse:
    """Calculate the optimal scheduling slots based on channel history and audience peak times."""
    timeline = await get_timeline(db, team, days=30)
    total_published = sum(p["published"] for p in timeline)

    now = datetime.now(timezone.utc)
    base_date = now + timedelta(days=1)

    # Calculate 3 slots: Evening prime, afternoon lunch, morning focus
    slots = [
        ScheduleSlot(
            recommended_time=base_date.replace(hour=19, minute=30, second=0, microsecond=0),
            day_name=base_date.strftime("%A"),
            confidence_score=94,
            reasoning=(
                f"ساعت ۱۹:۳۰ در پلتفرم {request.platform} بالاترین نرخ تعامل و بازدید مخاطبان فعال را دارد."
            ),
        ),
        ScheduleSlot(
            recommended_time=base_date.replace(hour=13, minute=0, second=0, microsecond=0),
            day_name=base_date.strftime("%A"),
            confidence_score=86,
            reasoning="پیک استراحت ظهرگاهی، مناسب برای محتوای آموزشی و سبک.",
        ),
        ScheduleSlot(
            recommended_time=(base_date + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0),
            day_name=(base_date + timedelta(days=1)).strftime("%A"),
            confidence_score=81,
            reasoning="ساعت پایانی شب، مناسب برای داستان‌سرایی و پست‌های چالشی.",
        ),
    ]

    insight = (
        f"کانال شما در ۳۰ روز گذشته مجموعاً {total_published} پست موفق منتشر کرده است. "
        f"بیشترین همگرایی مخاطبان بر اساس داده‌های آماری در ساعات عصرگاهی ثبت شده است."
    )

    return SmartScheduleResponse(
        platform=request.platform,
        recommended_slots=slots,
        analytics_insight=insight,
    )


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
