"""Pydantic schemas for analytics endpoints."""

from datetime import date, datetime

from pydantic import BaseModel


class PlatformStats(BaseModel):
    """Aggregated statistics for a single platform."""

    platform_code: str
    total: int
    published: int
    failed: int
    cancelled: int = 0
    success_rate: float


class TimelinePoint(BaseModel):
    """One day's aggregated counts."""

    date: date
    published: int
    failed: int
    cancelled: int = 0
    queued: int


class RecentJob(BaseModel):
    """Compact representation of a recent content job."""

    id: int
    platform_code: str
    status: str
    description_preview: str
    created_at: datetime
    scheduled_at: datetime | None = None


class SummaryResponse(BaseModel):
    """Top-level overview of content job activity."""

    total: int
    published: int
    failed: int
    cancelled: int = 0
    queued: int
    scheduled: int
    success_rate: float


class ByPlatformResponse(BaseModel):
    """Per-platform breakdown."""

    platforms: list[PlatformStats]


class TimelineResponse(BaseModel):
    """Daily timeline of job activity."""

    days: int
    points: list[TimelinePoint]


class RecentJobsResponse(BaseModel):
    """Most recent jobs for the team."""

    jobs: list[RecentJob]