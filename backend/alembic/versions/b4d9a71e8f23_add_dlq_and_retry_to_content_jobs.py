"""add dlq and retry columns to content_jobs

Revision ID: b4d9a71e8f23
Revises: e3f89a1b2c45
Create Date: 2026-09-12 02:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b4d9a71e8f23'
down_revision: Union[str, Sequence[str], None] = 'e3f89a1b2c45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add retry_count, max_retries, last_attempt_at, traceback_log, extra_metadata to content_jobs."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col['name'] for col in inspector.get_columns('content_jobs')]

    if 'retry_count' not in columns:
        op.add_column(
            'content_jobs',
            sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
        )

    if 'max_retries' not in columns:
        op.add_column(
            'content_jobs',
            sa.Column('max_retries', sa.Integer(), server_default='5', nullable=False),
        )

    if 'last_attempt_at' not in columns:
        op.add_column(
            'content_jobs',
            sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        )

    if 'traceback_log' not in columns:
        op.add_column(
            'content_jobs',
            sa.Column('traceback_log', sa.Text(), nullable=True),
        )

    if 'extra_metadata' not in columns:
        op.add_column(
            'content_jobs',
            sa.Column('extra_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        )


def downgrade() -> None:
    """Remove DLQ and retry columns from content_jobs."""
    op.drop_column('content_jobs', 'extra_metadata')
    op.drop_column('content_jobs', 'traceback_log')
    op.drop_column('content_jobs', 'last_attempt_at')
    op.drop_column('content_jobs', 'max_retries')
    op.drop_column('content_jobs', 'retry_count')
