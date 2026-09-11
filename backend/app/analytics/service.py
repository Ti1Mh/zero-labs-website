"""Analytics business logic (team-scoped SQL queries)."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TeamContext
from app.models.content import ContentJob
from app.models.platform import Platform


async def get_summary(db: AsyncSession, team: TeamContext) -> dict:
    """Return aggregate counts and success rate for the team."""
    stmt = select(
        func.count(ContentJob.id).label("total"),
        func.sum(case((ContentJob.status == "published", 1), else_=0)).label("published"),
        func.sum(case((ContentJob.status == "failed", 1), else_=0)).label("failed"),
        func.sum(case((ContentJob.status == "cancelled", 1), else_=0)).label("cancelled"),
        func.sum(case((ContentJob.status == "queued", 1), else_=0)).label("queued"),
        func.sum(case((ContentJob.status == "scheduled", 1), else_=0)).label("scheduled"),
    ).where(ContentJob.created_by == team.owner.id)

    result = await db.execute(stmt)
    row = result.one()

    total = row.total or 0
    published = row.published or 0
    failed = row.failed or 0
    cancelled = row.cancelled or 0
    queued = row.queued or 0
    scheduled = row.scheduled or 0

    terminal = published + failed
    success_rate = (published / terminal * 100) if terminal > 0 else 0.0

    return {
        "total": total,
        "published": published,
        "failed": failed,
        "cancelled": cancelled,
        "queued": queued,
        "scheduled": scheduled,
        "success_rate": round(success_rate, 2),
    }


async def get_by_platform(db: AsyncSession, team: TeamContext) -> list[dict]:
    """Return per-platform aggregate counts."""
    stmt = (
        select(
            Platform.code.label("platform_code"),
            func.count(ContentJob.id).label("total"),
            func.sum(case((ContentJob.status == "published", 1), else_=0)).label("published"),
            func.sum(case((ContentJob.status == "failed", 1), else_=0)).label("failed"),
            func.sum(case((ContentJob.status == "cancelled", 1), else_=0)).label("cancelled"),
        )
        .join(Platform, Platform.id == ContentJob.platform_id)
        .where(ContentJob.created_by == team.owner.id)
        .group_by(Platform.code)
        .order_by(func.count(ContentJob.id).desc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    platforms = []
    for row in rows:
        terminal = row.published + row.failed
        rate = (row.published / terminal * 100) if terminal > 0 else 0.0
        platforms.append({
            "platform_code": row.platform_code,
            "total": row.total,
            "published": row.published,
            "failed": row.failed,
            "cancelled": row.cancelled or 0,
            "success_rate": round(rate, 2),
        })
    return platforms


async def get_timeline(
    db: AsyncSession, team: TeamContext, days: int
) -> list[dict]:
    """Return daily counts for the last N days (zero-filled)."""
    if days < 1:
        days = 1
    if days > 365:
        days = 365

    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    stmt = (
        select(
            func.date(ContentJob.created_at).label("day"),
            func.sum(case((ContentJob.status == "published", 1), else_=0)).label("published"),
            func.sum(case((ContentJob.status == "failed", 1), else_=0)).label("failed"),
            func.sum(case((ContentJob.status == "cancelled", 1), else_=0)).label("cancelled"),
            func.sum(case((ContentJob.status == "queued", 1), else_=0)).label("queued"),
        )
        .where(
            ContentJob.created_by == team.owner.id,
            ContentJob.created_at >= start,
        )
        .group_by(func.date(ContentJob.created_at))
    )

    result = await db.execute(stmt)
    db_rows = {row.day: row for row in result.all()}

    points = []
    cursor = start.date()
    end = now.date()
    while cursor <= end:
        row = db_rows.get(cursor)
        points.append({
            "date": cursor,
            "published": int(row.published) if row else 0,
            "failed": int(row.failed) if row else 0,
            "cancelled": int(row.cancelled) if row and row.cancelled else 0,
            "queued": int(row.queued) if row else 0,
        })
        cursor += timedelta(days=1)

    return points


async def get_recent_jobs(
    db: AsyncSession, team: TeamContext, limit: int
) -> list[dict]:
    """Return the most recent jobs for the team."""
    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50

    stmt = (
        select(ContentJob, Platform.code.label("platform_code"))
        .join(Platform, Platform.id == ContentJob.platform_id)
        .where(ContentJob.created_by == team.owner.id)
        .order_by(ContentJob.created_at.desc())
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.all()

    jobs = []
    for job, platform_code in rows:
        preview = job.description
        if len(preview) > 120:
            preview = preview[:117] + "..."
        jobs.append({
            "id": job.id,
            "platform_code": platform_code,
            "status": job.status,
            "description_preview": preview,
            "created_at": job.created_at,
            "scheduled_at": job.scheduled_at,
        })
    return jobs