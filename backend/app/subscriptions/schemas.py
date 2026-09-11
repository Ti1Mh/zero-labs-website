"""Pydantic schemas for subscription plans and user subscriptions."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlanPriceOut(BaseModel):
    """Pricing information for a single period of a plan in one currency."""

    monthly: float
    yearly: float


class PlanOut(BaseModel):
    """Public representation of a subscription plan."""

    id: int
    name: str
    slug: str
    description: str | None = None
    prices: dict[str, PlanPriceOut] = Field(default_factory=dict)
    monthly_quota: int
    features: dict = Field(default_factory=dict)
    display_price: PlanPriceOut | None = None
    currency: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SubscriptionCreate(BaseModel):
    """Payload for purchasing a subscription. """

    plan_slug: str = Field(min_length=1, max_length=50)
    period: str = Field(pattern="^(monthly|yearly)$")
    currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")


class SubscriptionOut(BaseModel):
    """Public representation of a user's active subscription."""

    id: int
    plan: PlanOut
    status: str
    currency: str
    amount_paid: int = 0
    started_at: datetime
    expires_at: datetime
    auto_renew: bool
    days_remaining: int
    usage_this_month: int
    monthly_quota: int

    model_config = ConfigDict(from_attributes=True)


class CheckoutResult(BaseModel):
    """Result of a subscription purchase or cancellation."""

    success: bool
    subscription: SubscriptionOut | None = None
    message: str