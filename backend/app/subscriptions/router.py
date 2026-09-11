"""Subscription management endpoints: plan listing, purchase, and cancellation."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.geo.service import get_client_currency
from app.auth.dependencies import TeamContext, get_current_team
from app.core.database import get_db
from app.subscriptions.models import Plan
from app.subscriptions.schemas import (
    CheckoutResult,
    PlanOut,
    PlanPriceOut,
    SubscriptionCreate,
    SubscriptionOut,
)
from app.subscriptions.service import (
    cancel_subscription,
    get_current_subscription,
    get_monthly_usage,
    list_active_plans,
    subscribe,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def _resolve_display_price(plan: Plan, currency: str) -> tuple[PlanPriceOut | None, str]:
    """Pick the price for the requested currency, falling back to the first available."""
    if currency in plan.prices:
        p = plan.prices[currency]
        return PlanPriceOut(monthly=p["monthly"], yearly=p["yearly"]), currency
    if plan.prices:
        fallback = next(iter(plan.prices.keys()))
        p = plan.prices[fallback]
        return PlanPriceOut(monthly=p["monthly"], yearly=p["yearly"]), fallback
    return None, currency


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[PlanOut]:
    """List all active subscription plans with prices in the client's currency.
    
    Currency is auto-detected from IP (Iran→IRR, else→USD) unless the user
    is authenticated and has an explicit preference.
    """
    
    currency = await get_client_currency(request)
    plans = await list_active_plans(db)

    result = []
    for plan in plans:
        display_price, used_currency = _resolve_display_price(plan, currency)
        prices_out = {
            code: PlanPriceOut(monthly=p["monthly"], yearly=p["yearly"])
            for code, p in plan.prices.items()
        }
        result.append(PlanOut(
            id=plan.id,
            name=plan.name,
            slug=plan.slug,
            description=plan.description,
            prices=prices_out,
            monthly_quota=plan.monthly_quota,
            features=plan.features,
            display_price=display_price,
            currency=used_currency,
        ))
    return result


@router.post("/subscribe", response_model=CheckoutResult)
async def subscribe_endpoint(
    payload: SubscriptionCreate,
    request: Request,
    team: TeamContext = Depends(get_current_team),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResult:
    """Purchase a subscription (mock payment for MVP).
    
    Currency is auto-detected if not explicitly provided.
    """
    
    # Use provided currency, or user preference, or auto-detect
    if payload.currency:
        currency = payload.currency
    elif team.owner.default_currency:
        currency = team.owner.default_currency
    else:
        currency = await get_client_currency(request)
    
    sub = await subscribe(db, team.owner.id, payload.plan_slug, payload.period, currency)
    result = await db.execute(select(Plan).where(Plan.id == sub.plan_id))
    plan = result.scalar_one()
    usage = await get_monthly_usage(db, team.owner.id)
    sub_out = _build_subscription_out(sub, plan, usage)
    await db.commit()
    return CheckoutResult(
        success=True,
        subscription=sub_out,
        message="اشتراک شما با موفقیت فعال شد.",
    )


@router.get("/current", response_model=SubscriptionOut | None)
async def current_subscription(
    team: TeamContext = Depends(get_current_team),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionOut | None:
    """Get the user's current active subscription, or null if none."""
    sub = await get_current_subscription(db, team.owner.id)
    if sub is None:
        return None
    result = await db.execute(select(Plan).where(Plan.id == sub.plan_id))
    plan = result.scalar_one()
    usage = await get_monthly_usage(db, team.owner.id)
    return _build_subscription_out(sub, plan, usage)


@router.post("/cancel", response_model=CheckoutResult)
async def cancel_endpoint(
    team: TeamContext = Depends(get_current_team),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResult:
    """Cancel the current subscription (access remains until expiration)."""
    sub = await cancel_subscription(db, team.owner.id)
    result = await db.execute(select(Plan).where(Plan.id == sub.plan_id))
    plan = result.scalar_one()
    usage = await get_monthly_usage(db, team.owner.id)
    sub_out = _build_subscription_out(sub, plan, usage)
    await db.commit()
    return CheckoutResult(
        success=True,
        subscription=sub_out,
        message="اشتراک شما لغو شد. تا پایان دوره فعلی دسترسی دارید.",
    )


def _build_subscription_out(sub, plan: Plan, usage: int) -> SubscriptionOut:
    """Build a SubscriptionOut response from DB entities."""
    now = datetime.now(timezone.utc)
    days_remaining = max(0, (sub.expires_at - now).days)
    display_price, currency = _resolve_display_price(plan, sub.currency)
    prices_out = {
        code: PlanPriceOut(monthly=p["monthly"], yearly=p["yearly"])
        for code, p in plan.prices.items()
    }
    plan_out = PlanOut(
        id=plan.id,
        name=plan.name,
        slug=plan.slug,
        description=plan.description,
        prices=prices_out,
        monthly_quota=plan.monthly_quota,
        features=plan.features,
        display_price=display_price,
        currency=currency,
    )
    return SubscriptionOut(
        id=sub.id,
        plan=plan_out,
        status=sub.status,
        currency=sub.currency,
        amount_paid=getattr(sub, "amount_paid", 0),
        started_at=sub.started_at,
        expires_at=sub.expires_at,
        auto_renew=sub.auto_renew,
        days_remaining=days_remaining,
        usage_this_month=usage,
        monthly_quota=plan.monthly_quota,
    )