"""Service layer for Super Admin operations, Plan CRUD, User oversight, and AI Ledger querying."""

import inspect
import logging
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import (
    AILedgerEntryResponse,
    AILedgerSummaryResponse,
    AdminPlanCreate,
    AdminPlanUpdate,
    UpdateUserStatusRequest,
)
from app.ai.ledger_models import AIUsageLedger
from app.auth.models import User
from app.core.exceptions import ConflictError, NotFoundError
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


async def update_user_status_admin(
    db: AsyncSession,
    user_id: int,
    req: UpdateUserStatusRequest,
) -> User:
    """Update user active or superuser status."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if inspect.isawaitable(user):
        user = await user
    if not user:
        raise NotFoundError("کاربر مورد نظر یافت نشد.")

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
