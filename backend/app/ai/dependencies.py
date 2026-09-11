"""FastAPI dependencies for AI rate limiting and spend circuit breaker."""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TeamContext, require
from app.core.database import get_db
from app.core.rate_limit import check_ai_spend_circuit_breaker, enforce_ai_burst_limit


async def enforce_ai_guardrails(
    request: Request,
    team: TeamContext = Depends(require("ai:use")),
    db: AsyncSession = Depends(get_db),
) -> TeamContext:
    """Enforce per-user burst rate limit and team owner spend circuit breaker before generation."""
    redis_client = getattr(request.app.state, "redis", None)

    # 1. Burst Limit: 10 requests / 60 seconds per user
    await enforce_ai_burst_limit(redis_client, team.current_user.id)

    # 2. Spend Circuit Breaker: Daily cost cap check
    await check_ai_spend_circuit_breaker(redis_client, db, team.owner.id)

    return team
