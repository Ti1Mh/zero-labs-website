"""Subscription business logic: plan listing, purchasing, and quota tracking."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from app.models.content import ContentJob
from app.subscriptions.models import Plan, Subscription


async def list_active_plans(db: AsyncSession) -> list[Plan]:
    """Return all active subscription plans, ordered by name."""
    result = await db.execute(
        select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.name)
    )
    return list(result.scalars().all())


async def get_current_subscription(
    db: AsyncSession, user_id: int
) -> Subscription | None:
    """Return the user's active (non-expired) subscription, if any."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status.in_(("active", "trial")),
            Subscription.expires_at > now,
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_monthly_usage(db: AsyncSession, user_id: int) -> int:
    """Count posts created by the user in the current calendar month (UTC)."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count(ContentJob.id)).where(
            ContentJob.created_by == user_id,
            ContentJob.created_at >= month_start,
        )
    )
    return result.scalar_one() or 0


async def subscribe(
    db: AsyncSession,
    user_id: int,
    plan_slug: str,
    period: str,
    currency: str,
) -> Subscription:
    """Create a new active subscription for the user (mock payment)."""
    result = await db.execute(
        select(Plan).where(Plan.slug == plan_slug, Plan.is_active.is_(True))
    )
    plan = result.scalar_one()
    if plan is None:
        raise NotFoundError("پلن یافت نشد یا غیرفعال است.")

    # Validate currency is supported by this plan
    if currency not in plan.prices:
        supported = ", ".join(plan.prices.keys())
        raise InvalidInputError(
            f"ارز {currency} برای این پلن پشتیبانی نمی‌شود. ارزهای پشتیبانی‌شده: {supported}"
        )

    if period not in plan.prices[currency]:
        raise InvalidInputError(f"دوره {period} برای این پلن معتبر نیست.")

    raw_price = plan.prices[currency][period]
    if currency == "USD":
        amount_paid = int(round(float(raw_price) * 100))
    else:
        amount_paid = int(round(float(raw_price)))

    # Prevent double subscription
    current = await get_current_subscription(db, user_id)
    if current is not None:
        raise ConflictError(
            "شما در حال حاضر اشتراک فعال دارید. ابتدا آن را لغو کنید."
        )

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=30 if period == "monthly" else 365)

    sub = Subscription(
        user_id=user_id,
        plan_id=plan.id,
        status="active",
        currency=currency,
        amount_paid=amount_paid,
        started_at=now,
        expires_at=expires,
        auto_renew=True,
        payment_method="mock",
        external_id=f"mock_{int(now.timestamp())}",
    )
    db.add(sub)
    await db.flush()
    return sub


async def cancel_subscription(
    db: AsyncSession, user_id: int
) -> Subscription:
    """Cancel the user's active subscription (keep access until expiration)."""
    current = await get_current_subscription(db, user_id)
    if current is None:
        raise NotFoundError("اشتراک فعالی یافت نشد.")
    current.status = "cancelled"
    current.auto_renew = False
    await db.flush()
    return current