"""FastAPI router for Super Admin backoffice endpoints."""

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import (
    AILedgerSummaryResponse,
    AdminPlanCreate,
    AdminPlanResponse,
    AdminPlanUpdate,
    AdminUserItem,
    DLQJobListResponse,
    RetryJobResponse,
    UpdateUserStatusRequest,
)
from app.admin.service import (
    create_plan_admin,
    get_ai_ledger_summary,
    get_plan_admin,
    list_dlq_jobs_admin,
    list_plans_admin,
    list_users_admin,
    retry_dlq_job_admin,
    toggle_plan_status_admin,
    update_plan_admin,
    update_user_status_admin,
)
from app.auth.dependencies import require_superuser
from app.auth.models import User
from app.core.database import get_db

router = APIRouter(
    prefix="/admin",
    tags=["Super Admin Backoffice"],
    dependencies=[Depends(require_superuser)],
)


# --- Plan Management Endpoints ---

@router.get("/plans", response_model=list[AdminPlanResponse])
async def list_plans_endpoint(
    include_inactive: bool = Query(default=True, description="Include deactivated plans"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """List all subscription plans with pricing and quota settings."""
    plans = await list_plans_admin(db, include_inactive=include_inactive)
    return [AdminPlanResponse.model_validate(p) for p in plans]


@router.post("/plans", response_model=AdminPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan_endpoint(
    request: AdminPlanCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Create a new subscription plan with dynamic pricing and features."""
    plan = await create_plan_admin(db, request)
    return AdminPlanResponse.model_validate(plan)


@router.get("/plans/{plan_id}", response_model=AdminPlanResponse)
async def get_plan_endpoint(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Retrieve full configuration of a subscription plan."""
    plan = await get_plan_admin(db, plan_id)
    return AdminPlanResponse.model_validate(plan)


@router.patch("/plans/{plan_id}", response_model=AdminPlanResponse)
async def update_plan_endpoint(
    plan_id: int,
    request: AdminPlanUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Update plan prices (IRR/USD), quotas, features, or active flag."""
    plan = await update_plan_admin(db, plan_id, request)
    return AdminPlanResponse.model_validate(plan)


@router.post("/plans/{plan_id}/toggle", response_model=AdminPlanResponse)
async def toggle_plan_status_endpoint(
    plan_id: int,
    is_active: bool = Query(..., description="Target active status"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Activate or deactivate a plan without modifying price structure."""
    plan = await toggle_plan_status_admin(db, plan_id, is_active=is_active)
    return AdminPlanResponse.model_validate(plan)


# --- User Oversight Endpoints ---

@router.get("/users", response_model=list[AdminUserItem])
async def list_users_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """List registered users with status flags."""
    users = await list_users_admin(db, limit=limit, offset=offset)
    return [AdminUserItem.model_validate(u) for u in users]


@router.patch("/users/{user_id}", response_model=AdminUserItem)
async def update_user_status_endpoint(
    user_id: int,
    request: UpdateUserStatusRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Update user activation or superuser permissions."""
    user = await update_user_status_admin(db, user_id, request)
    return AdminUserItem.model_validate(user)


# --- AI Ledger Oversight ---

@router.get("/ai-ledger", response_model=AILedgerSummaryResponse)
async def get_ai_ledger_endpoint(
    user_id: int | None = Query(default=None, description="Filter by user ID"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Query append-only AI usage ledger and aggregate token/cost totals."""
    return await get_ai_ledger_summary(db, user_id=user_id, limit=limit, offset=offset)


# --- Dead Letter Queue (DLQ) Oversight Endpoints ---

@router.get("/jobs/dlq", response_model=DLQJobListResponse)
async def list_dlq_jobs_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """List permanently failed jobs stored in the Dead Letter Queue."""
    return await list_dlq_jobs_admin(db, limit=limit, offset=offset)


@router.post("/jobs/{job_id}/retry", response_model=RetryJobResponse)
async def retry_dlq_job_endpoint(
    job_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_superuser),
):
    """Replay a failed DLQ job back into the active processing queue."""
    redis_pool = getattr(request.app.state, "redis", None)
    return await retry_dlq_job_admin(db, job_id=job_id, redis_pool=redis_pool)

