"""Service layer for Super Admin operations, Plan CRUD, User oversight, and AI Ledger querying."""

import inspect
import logging
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import (
    AILedgerEntryResponse,
    AILedgerSummaryResponse,
    AdminCreateUserRequest,
    AdminPlanCreate,
    AdminPlanUpdate,
    DLQJobItemResponse,
    DLQJobListResponse,
    RetryJobResponse,
    UpdateUserStatusRequest,
)
from app.ai.ledger_models import AIUsageLedger
from app.auth.models import User
from app.auth.security import hash_password
from app.core.exceptions import ConflictError, NotFoundError
from app.models.content import ContentJob
from app.subscriptions.models import Plan

logger = logging.getLogger(__name__)


# --- Plan CRUD ---

async def list_plans_admin(
    db: AsyncSession,
    include_inactive: bool = True,
) -> list[Plan]:
    """Retrieve all plans in the system for Super Admin oversight."""
    query = select(Plan).order_by(Plan.id.asc())
    if not include_inactive:
        query = query.where(Plan.is_active.is_(True))

    result = await db.execute(query)
    scalars = result.scalars()
    if inspect.isawaitable(scalars):
        scalars = await scalars
    plans = scalars.all()
    if inspect.isawaitable(plans):
        plans = await plans
    return list(plans)


async def get_plan_admin(db: AsyncSession, plan_id: int) -> Plan:
    """Retrieve a single plan by ID or raise NotFoundError."""
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    res = result.scalar_one_or_none()
    if inspect.isawaitable(res):
        res = await res
    if not res:
        raise NotFoundError("پلن مورد نظر یافت نشد.")
    return res


async def create_plan_admin(
    db: AsyncSession,
    req: AdminPlanCreate,
) -> Plan:
    """Create a new subscription plan."""
    # Check for existing slug or name
    existing_result = await db.execute(
        select(Plan).where((Plan.slug == req.slug) | (Plan.name == req.name))
    )
    existing = existing_result.scalar_one_or_none()
    if inspect.isawaitable(existing):
        existing = await existing
    if existing:
        raise ConflictError("پلن با این نام یا شناسه‌ی slug از قبل وجود دارد.")

    new_plan = Plan(
        name=req.name,
        slug=req.slug,
        description=req.description,
        prices=req.prices,
        monthly_quota=req.monthly_quota,
        features=req.features,
        is_active=req.is_active,
    )
    res = db.add(new_plan)
    if inspect.isawaitable(res):
        await res
    await db.flush()
    return new_plan


async def update_plan_admin(
    db: AsyncSession,
    plan_id: int,
    req: AdminPlanUpdate,
) -> Plan:
    """Update existing subscription plan attributes, pricing, or quotas."""
    plan = await get_plan_admin(db, plan_id)

    if req.name is not None:
        plan.name = req.name
    if req.description is not None:
        plan.description = req.description
    if req.prices is not None:
        plan.prices = req.prices
    if req.monthly_quota is not None:
        plan.monthly_quota = req.monthly_quota
    if req.features is not None:
        plan.features = req.features
    if req.is_active is not None:
        plan.is_active = req.is_active

    await db.flush()
    return plan


async def toggle_plan_status_admin(
    db: AsyncSession,
    plan_id: int,
    is_active: bool,
) -> Plan:
    """Activate or deactivate a plan without deleting historical subscriptions."""
    plan = await get_plan_admin(db, plan_id)
    plan.is_active = is_active
    await db.flush()
    return plan


# --- User Oversight ---

async def list_users_admin(
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[User]:
    """List registered users with superuser and activity flags."""
    result = await db.execute(
        select(User).order_by(User.id.desc()).limit(limit).offset(offset)
    )
    scalars = result.scalars()
    if inspect.isawaitable(scalars):
        scalars = await scalars
    users = scalars.all()
    if inspect.isawaitable(users):
        users = await users
    return list(users)


def _normalize_phone(phone: str) -> str:
    """Normalize phone to international format."""
    clean = phone.strip()
    if clean.startswith("0098"):
        return "+98" + clean[4:]
    if clean.startswith("09"):
        return "+98" + clean[1:]
    if not clean.startswith("+"):
        return "+98" + clean
    return clean


async def create_user_admin(
    db: AsyncSession,
    req: AdminCreateUserRequest,
) -> User:
    """Create a new user or administrator directly, or promote existing user."""
    norm_phone = _normalize_phone(req.phone)
    res = await db.execute(select(User).where(User.phone_number == norm_phone))
    user = res.scalar_one_or_none()
    if inspect.isawaitable(user):
        user = await user

    if user is not None:
        user.is_superuser = req.is_superuser
        user.is_active = req.is_active
        user.is_verified = True
        if req.display_name:
            user.display_name = req.display_name
        if req.password:
            user.password_hash = hash_password(req.password)
    else:
        user = User(
            phone_number=norm_phone,
            password_hash=hash_password(req.password),
            display_name=req.display_name,
            is_verified=True,
            is_active=req.is_active,
            is_superuser=req.is_superuser,
            owner_user_id=None,
        )
        db.add(user)

    await db.flush()
    return user


async def update_user_status_admin(
    db: AsyncSession,
    user_id: int,
    req: UpdateUserStatusRequest,
) -> User:
    """Update user active or superuser status, credentials, or phone number."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if inspect.isawaitable(user):
        user = await user
    if not user:
        raise NotFoundError("کاربر مورد نظر یافت نشد.")

    if req.phone_number is not None:
        norm_phone = _normalize_phone(req.phone_number)
        dup = await db.execute(
            select(User).where(User.phone_number == norm_phone, User.id != user_id)
        )
        dup_user = dup.scalar_one_or_none()
        if inspect.isawaitable(dup_user):
            dup_user = await dup_user
        if dup_user is not None:
            raise ConflictError("این شماره موبایل توسط کاربر دیگری استفاده شده است.")
        user.phone_number = norm_phone

    if req.password is not None:
        user.password_hash = hash_password(req.password)
    if req.display_name is not None:
        user.display_name = req.display_name
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.is_superuser is not None:
        user.is_superuser = req.is_superuser

    await db.flush()
    return user


# --- AI Ledger Aggregation & Audit ---

async def get_ai_ledger_summary(
    db: AsyncSession,
    user_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> AILedgerSummaryResponse:
    """Query append-only ledger entries and compute aggregates."""
    query = select(AIUsageLedger).order_by(AIUsageLedger.id.desc())
    if user_id is not None:
        query = query.where(AIUsageLedger.user_id == user_id)

    # Fetch paginated items
    paginated_query = query.limit(limit).offset(offset)
    res = await db.execute(paginated_query)
    scalars = res.scalars()
    if inspect.isawaitable(scalars):
        scalars = await scalars
    entries = scalars.all()
    if inspect.isawaitable(entries):
        entries = await entries

    # Fetch totals
    totals_query = select(
        func.count(AIUsageLedger.id),
        func.coalesce(func.sum(AIUsageLedger.tokens_prompt), 0),
        func.coalesce(func.sum(AIUsageLedger.tokens_completion), 0),
        func.coalesce(func.sum(AIUsageLedger.cost_cents), 0),
    )
    if user_id is not None:
        totals_query = totals_query.where(AIUsageLedger.user_id == user_id)

    tot_res = await db.execute(totals_query)
    tot_row = tot_res.one_or_none()
    if inspect.isawaitable(tot_row):
        tot_row = await tot_row

    if tot_row:
        total_invocations = tot_row[0] or 0
        total_tokens_prompt = tot_row[1] or 0
        total_tokens_completion = tot_row[2] or 0
        total_cost_cents = tot_row[3] or 0
    else:
        total_invocations = len(entries)
        total_tokens_prompt = sum(e.tokens_prompt for e in entries)
        total_tokens_completion = sum(e.tokens_completion for e in entries)
        total_cost_cents = sum(e.cost_cents for e in entries)

    items = [AILedgerEntryResponse.model_validate(e) for e in entries]

    return AILedgerSummaryResponse(
        total_invocations=total_invocations,
        total_tokens_prompt=total_tokens_prompt,
        total_tokens_completion=total_tokens_completion,
        total_cost_cents=total_cost_cents,
        items=items,
    )


# --- Dead Letter Queue (DLQ) Oversight ---

async def list_dlq_jobs_admin(
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> DLQJobListResponse:
    """List jobs that have failed and transitioned to Dead Letter Queue (DLQ)."""
    count_query = select(func.count(ContentJob.id)).where(ContentJob.status == "failed")
    count_res = await db.execute(count_query)
    count_scalar = count_res.scalar()
    if inspect.isawaitable(count_scalar):
        count_scalar = await count_scalar
    total = count_scalar or 0

    jobs_query = (
        select(ContentJob)
        .where(ContentJob.status == "failed")
        .order_by(ContentJob.last_attempt_at.desc().nullslast(), ContentJob.id.desc())
        .limit(limit)
        .offset(offset)
    )
    res = await db.execute(jobs_query)
    scalars = res.scalars()
    if inspect.isawaitable(scalars):
        scalars = await scalars
    jobs = scalars.all()
    if inspect.isawaitable(jobs):
        jobs = await jobs

    items = [DLQJobItemResponse.model_validate(j) for j in jobs]
    return DLQJobListResponse(total=total, items=items)


async def retry_dlq_job_admin(
    db: AsyncSession,
    job_id: int,
    redis_pool: object = None,
) -> RetryJobResponse:
    """Replay a failed DLQ job back into the active processing queue."""
    result = await db.execute(select(ContentJob).where(ContentJob.id == job_id))
    job = result.scalar_one_or_none()
    if inspect.isawaitable(job):
        job = await job
    if not job:
        raise NotFoundError("جاب مورد نظر یافت نشد.")

    job.status = "queued"
    job.retry_count = 0
    job.error_message = None
    job.traceback_log = None
    await db.flush()

    if redis_pool is not None:
        enqueue_res = redis_pool.enqueue_job("publish_content", job.id)
        if inspect.isawaitable(enqueue_res):
            await enqueue_res

    return RetryJobResponse(
        job_id=job.id,
        status="queued",
        message="جاب با موفقیت مجدداً به صف پردازش ارسال شد.",
    )

