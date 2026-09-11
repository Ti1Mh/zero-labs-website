"""add amount_paid to subscriptions

Revision ID: d5d8fa8434e3
Revises: 94aeb74aeb80
Create Date: 2026-09-11 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5d8fa8434e3'
down_revision: Union[str, Sequence[str], None] = '94aeb74aeb80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add amount_paid column to subscriptions table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = [col['name'] for col in inspector.get_columns('subscriptions')]
    if 'amount_paid' not in existing_columns:
        op.add_column(
            'subscriptions',
            sa.Column('amount_paid', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    """Remove amount_paid column from subscriptions table."""
    op.drop_column('subscriptions', 'amount_paid')
