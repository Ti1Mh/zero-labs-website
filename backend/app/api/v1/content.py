"""Content job endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import TeamContext, require
from app.core.database import get_db
from app.models.content import ContentJob
from app.models.platform import Platform
from app.schemas.content import (
    ContentDetailResponse,
    ContentListResponse,
    ContentRequest,
    ContentResponse,
)


router = APIRouter(prefix="/content", tags=["content"])


@router.post("", response_model=ContentResponse, status_code=status.HTTP_201_CREATED)
async def create_content_job(
    payload: ContentRequest,
    request: Request,
    team: TeamContext = Depends(require("content:create")),
    db: AsyncSession = Depends(get_db),
) -> ContentResponse:
    """Queue a content job (immediately or scheduled) after validating the target platform."""

    # 1) Idempotency guard
    if payload.idempotency_key is not None:
        result = await db.execute(
            select(ContentJob).where(
                ContentJob.client_idempotency_key == payload.idempotency_key
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            plat = await db.execute(select(Platform).where(Platform.id == existing.platform_id))
            return JSONResponse(
                status_code=200,
                content={
                    "id": existing.id,
                    "status": existing.status,
                    "platform_code": plat.scalar_one().code,
                    "created_at": existing.created_at.isoformat(),
                    "scheduled_at": (
                        existing.scheduled_at.isoformat()
                        if existing.scheduled_at is not None
                        else None
                    ),
                },
            )

    # 2) Resolve platform
    result = await db.execute(
        select(Platform).where(Platform.code == payload.platform_code)
    )
    platform = result.scalar_one_or_none()
    if platform is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown platform code.")
    if not platform.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Platform is currently disabled.")

    # 3) Validate scheduled_at
    now = datetime.now(timezone.utc)
    if payload.scheduled_at is not None:
        scheduled = payload.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        if scheduled <= now:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "scheduled_at must be in the future.",
            )

    # 4) Decide initial status based on schedule
    job_status_value = "scheduled" if payload.scheduled_at is not None else "queued"

    # 5) Create the job
    job = ContentJob(
        description=payload.description,
        platform_id=platform.id,
        status=job_status_value,
        scheduled_at=payload.scheduled_at,
        client_idempotency_key=payload.idempotency_key,
        created_by=team.current_user.id,
    )
    db.add(job)
    await db.flush()

    # 6) Snapshot before commit (attributes detach after commit)
    job_id = job.id
    job_status = job.status
    job_created_at = job.created_at
    job_scheduled_at = job.scheduled_at

    await db.commit()

    # 7) Only enqueue immediate jobs; scheduled jobs wait for the cron
    if job_status == "queued":
        await request.app.state.redis.enqueue_job("publish_content", job_id)

    return ContentResponse(
        id=job_id,
        status=job_status,
        platform_code=platform.code,
        created_at=job_created_at,
        scheduled_at=job_scheduled_at,
    )


@router.get("", response_model=ContentListResponse)
async def list_content_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(queued|scheduled|processing|published|failed|cancelled)$",
    ),
    platform_code: str | None = Query(default=None, max_length=50),
    team: TeamContext = Depends(require("content:view")),
    db: AsyncSession = Depends(get_db),
) -> ContentListResponse:
    """List content jobs with pagination and filters (scoped to creator)."""
    # Base query (join platform for code, filter by owner)
    stmt = (
        select(ContentJob)
        .options(selectinload(ContentJob.platform))
        .where(ContentJob.created_by == team.current_user.id)
    )

    if status_filter:
        stmt = stmt.where(ContentJob.status == status_filter)
    if platform_code:
        stmt = stmt.join(Platform).where(Platform.code == platform_code)

    # Total count (independent of pagination)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate + order
    stmt = stmt.order_by(ContentJob.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()

    items = [
        ContentResponse(
            id=job.id,
            status=job.status,
            platform_code=job.platform.code,
            created_at=job.created_at,
            scheduled_at=job.scheduled_at,
        )
        for job in rows
    ]

    return ContentListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


@router.get("/{job_id}", response_model=ContentDetailResponse)
async def get_content_job(
    job_id: int,
    team: TeamContext = Depends(require("content:view")),
    db: AsyncSession = Depends(get_db),
) -> ContentDetailResponse:
    """Return detailed state of a content job (only if owned by the user)."""
    result = await db.execute(
        select(ContentJob)
        .options(selectinload(ContentJob.platform))
        .where(
            ContentJob.id == job_id,
            ContentJob.created_by == team.current_user.id,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")

    return ContentDetailResponse(
        id=job.id,
        status=job.status,
        platform_code=job.platform.code,
        description=job.description,
        created_at=job.created_at,
        scheduled_at=job.scheduled_at,
        error_message=job.error_message,
        updated_at=job.updated_at,
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_content_job(
    job_id: int,
    team: TeamContext = Depends(require("content:cancel")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Cancel a queued or scheduled job (mark as cancelled with a reason)."""
    result = await db.execute(
        select(ContentJob).where(
            ContentJob.id == job_id,
            ContentJob.created_by == team.current_user.id,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
    if job.status not in ("queued", "scheduled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only queued or scheduled jobs can be cancelled.",
        )

    job.status = "cancelled"
    job.error_message = "Cancelled by user"
    await db.commit()