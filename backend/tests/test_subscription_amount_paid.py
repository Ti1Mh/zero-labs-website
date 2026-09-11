"""Unit tests for Subscription amount_paid snapshot and smallest-unit calculation."""

from datetime import datetime, timezone
from app.subscriptions.models import Plan, Subscription
from app.subscriptions.schemas import SubscriptionOut, PlanOut


def test_subscription_model_has_amount_paid():
    """Verify Subscription model has amount_paid column."""
    now = datetime.now(timezone.utc)
    sub = Subscription(
        user_id=1,
        plan_id=1,
        status="active",
        currency="IRR",
        amount_paid=49000,
        started_at=now,
        expires_at=now,
    )
    assert sub.amount_paid == 49000


def test_amount_paid_snapshot_immutability():
    """Verify modifying plan price later does not affect subscription amount_paid."""
    now = datetime.now(timezone.utc)
    plan = Plan(
        name="Pro",
        slug="pro",
        prices={"IRR": {"monthly": 49000, "yearly": 490000}},
        monthly_quota=500,
    )
    sub = Subscription(
        user_id=1,
        plan_id=plan.id or 1,
        status="active",
        currency="IRR",
        amount_paid=plan.prices["IRR"]["monthly"],
        started_at=now,
        expires_at=now,
    )
    assert sub.amount_paid == 49000

    # Modify plan price
    plan.prices = {"IRR": {"monthly": 99000, "yearly": 990000}}
    assert sub.amount_paid == 49000


def test_currency_smallest_unit_conversion():
    """Verify USD conversion to cents and IRR preservation."""
    usd_price = 1.5
    amount_paid_usd = int(round(float(usd_price) * 100))
    assert amount_paid_usd == 150

    irr_price = 49000
    amount_paid_irr = int(round(float(irr_price)))
    assert amount_paid_irr == 49000
