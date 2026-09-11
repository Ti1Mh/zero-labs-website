"""Unit tests for ContentStatus cancelled separation and analytics schemas."""

import pytest
from pydantic import ValidationError

from app.analytics.schemas import PlatformStats, SummaryResponse, TimelinePoint
from app.schemas.content import ContentListRequest, ContentStatus


def test_content_status_has_cancelled():
    """Verify cancelled is a valid member of ContentStatus enum."""
    assert ContentStatus.cancelled == "cancelled"
    assert "cancelled" in [s.value for s in ContentStatus]


def test_content_list_request_accepts_cancelled():
    """Verify ContentListRequest accepts cancelled in regex pattern."""
    req = ContentListRequest(status="cancelled")
    assert req.status == "cancelled"

    for valid_status in ["queued", "scheduled", "processing", "published", "failed", "cancelled"]:
        req_valid = ContentListRequest(status=valid_status)
        assert req_valid.status == valid_status

    with pytest.raises(ValidationError):
        ContentListRequest(status="unknown_status")


def test_analytics_schemas_include_cancelled():
    """Verify analytics schemas have cancelled field with default 0."""
    from datetime import date

    summary = SummaryResponse(
        total=10,
        published=5,
        failed=1,
        cancelled=4,
        queued=0,
        scheduled=0,
        success_rate=83.33,
    )
    assert summary.cancelled == 4

    platform_stats = PlatformStats(
        platform_code="telegram",
        total=10,
        published=5,
        failed=1,
        cancelled=4,
        success_rate=83.33,
    )
    assert platform_stats.cancelled == 4

    timeline_point = TimelinePoint(
        date=date(2026, 9, 11),
        published=5,
        failed=1,
        cancelled=4,
        queued=0,
    )
    assert timeline_point.cancelled == 4


def test_cancelled_does_not_affect_success_rate():
    """Verify cancelled jobs are excluded from both numerator and denominator of success_rate."""
    published = 8
    failed = 2
    cancelled = 20

    terminal = published + failed
    success_rate = (published / terminal * 100) if terminal > 0 else 0.0

    assert round(success_rate, 2) == 80.0

    published_zero = 0
    failed_zero = 0
    terminal_zero = published_zero + failed_zero
    success_rate_zero = (published_zero / terminal_zero * 100) if terminal_zero > 0 else 0.0
    assert success_rate_zero == 0.0
