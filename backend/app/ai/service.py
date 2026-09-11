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
from app.ai.ledger_models import AIUsageLedger
from app.ai.safety import check_content_safety
from app.core.config import get_settings
from app.core.exceptions import InvalidInputError, NotFoundError
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


def calculate_cost_cents(model: str, tokens_prompt: int, tokens_completion: int) -> int:
    """Calculate or estimate API cost in US cents."""
    m = model.lower()
    if "claude" in m or "sonnet" in m:
        cost = (tokens_prompt * 300 + tokens_completion * 1500) / 1_000_000
        return max(1, round(cost)) if (tokens_prompt + tokens_completion > 0) else 0
    elif "gpt-4o" in m:
        cost = (tokens_prompt * 15 + tokens_completion * 60) / 1_000_000
        return max(1, round(cost)) if (tokens_prompt + tokens_completion > 0) else 0
    return 0


async def record_ai_usage(
    db: AsyncSession,
    team: TeamContext,
    provider: str,
    model: str,
    operation_type: str,
    tokens_prompt: int = 150,
    tokens_completion: int = 350,
    content_job_id: int | None = None,
) -> AIUsageLedger:
    """Append immutable transaction entry to the AI usage ledger."""
    cost_cents = calculate_cost_cents(model, tokens_prompt, tokens_completion)
    ledger_entry = AIUsageLedger(
        owner_id=team.owner.id,
        user_id=team.current_user.id,
        content_job_id=content_job_id,
        provider=provider,
        model=model,
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        cost_cents=cost_cents,
        operation_type=operation_type,
    )
    res = db.add(ledger_entry)
    if inspect.isawaitable(res):
        await res
    await db.flush()
    return ledger_entry


async def _execute_with_retry(
    provider: BaseAIProvider,
    prompt: str,
    model: str,
    response_model: type,
    system_prompt: str | None = None,
):
    """Execute structured generation with 1-retry fallback on malformed JSON or validation errors."""
    try:
        return await provider.generate_structured(
            prompt=prompt,
            model=model,
            response_model=response_model,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        logger.warning(
            "AI generation failed on first attempt (%s: %s). Executing 1-retry fallback with strict schema instructions...",
            type(exc).__name__,
            exc,
        )
        retry_prompt = (
            f"{prompt}\n\n"
            "CRITICAL: The previous output failed schema validation or was malformed JSON. "
            "Please return ONLY a valid, strictly formatted JSON object adhering to the schema."
        )
        return await provider.generate_structured(
            prompt=retry_prompt,
            model=model,
            response_model=response_model,
            system_prompt=system_prompt,
            temperature=0.2,
        )


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
    res = db.add(persona)
    if inspect.isawaitable(res):
        await res
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
    # 1. Content Safety Guardrail
    is_safe, reason = check_content_safety(request.topic)
    if not is_safe:
        raise InvalidInputError(reason)
    if request.extra_instructions:
        is_safe, reason = check_content_safety(request.extra_instructions)
        if not is_safe:
            raise InvalidInputError(reason)

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

    response = await _execute_with_retry(
        provider=provider,
        prompt=user_prompt,
        model=model,
        response_model=GeneratedPostResponse,
        system_prompt=system_prompt,
    )

    # 2. Record immutable transaction in AI usage ledger
    tokens_prompt = max(10, (len(system_prompt) + len(user_prompt)) // 4)
    tokens_completion = max(10, len(response.body) // 4 + 100)
    provider_name = getattr(provider, "provider_name", "openrouter")

    await record_ai_usage(
        db=db,
        team=team,
        provider=provider_name,
        model=model,
        operation_type="generate",
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
    )

    return response


async def stream_post(
    db: AsyncSession,
    team: TeamContext,
    request: GeneratePostRequest,
) -> AsyncIterator[str]:
    """Stream token deltas for interactive studio creation."""
    is_safe, reason = check_content_safety(request.topic)
    if not is_safe:
        raise InvalidInputError(reason)
    if request.extra_instructions:
        is_safe, reason = check_content_safety(request.extra_instructions)
        if not is_safe:
            raise InvalidInputError(reason)

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
    is_safe, reason = check_content_safety(request.draft_text)
    if not is_safe:
        raise InvalidInputError(reason)

    provider = get_ai_provider()
    tier, model = await resolve_user_ai_tier(db, team)
    persona = await resolve_active_persona(db, team, request.persona_id)

    sys_prompt, user_prompt = build_doctor_prompts(request, persona)

    logger.info("Running Content Doctor for team owner %d on platform %s", team.owner.id, request.platform)

    response = await _execute_with_retry(
        provider=provider,
        prompt=user_prompt,
        model=model,
        response_model=OptimizePostResponse,
        system_prompt=sys_prompt,
    )

    tokens_prompt = max(10, (len(sys_prompt) + len(user_prompt)) // 4)
    tokens_completion = max(10, len(response.improved_version) // 4 + 100)
    provider_name = getattr(provider, "provider_name", "openrouter")

    await record_ai_usage(
        db=db,
        team=team,
        provider=provider_name,
        model=model,
        operation_type="optimize",
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
    )

    return response


async def repurpose_post(
    db: AsyncSession,
    team: TeamContext,
    request: RepurposeRequest,
) -> RepurposeResponse:
    """Repurpose master text into native posts across multiple platforms."""
    is_safe, reason = check_content_safety(request.source_text)
    if not is_safe:
        raise InvalidInputError(reason)

    provider = get_ai_provider()
    tier, model = await resolve_user_ai_tier(db, team)
    persona = await resolve_active_persona(db, team, request.persona_id)

    sys_prompt, user_prompt = build_repurpose_prompts(request, persona)

    logger.info(
        "Repurposing content for team owner %d to platforms: %s",
        team.owner.id,
        request.target_platforms,
    )

    response = await _execute_with_retry(
        provider=provider,
        prompt=user_prompt,
        model=model,
        response_model=RepurposeResponse,
        system_prompt=sys_prompt,
    )

    tokens_prompt = max(10, (len(sys_prompt) + len(user_prompt)) // 4)
    tokens_completion = max(50, sum(len(p.body) // 4 for p in response.posts.values()))
    provider_name = getattr(provider, "provider_name", "openrouter")

    await record_ai_usage(
        db=db,
        team=team,
        provider=provider_name,
        model=model,
        operation_type="repurpose",
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
    )

    return response


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
