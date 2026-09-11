"""add superuser, ai_usage_ledger, and support tickets

Revision ID: e3f89a1b2c45
Revises: c7a42b10de88
Create Date: 2026-09-11 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e3f89a1b2c45'
down_revision: Union[str, Sequence[str], None] = 'c7a42b10de88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_superuser column, ai_usage_ledger, and support tables."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = [col['name'] for col in inspector.get_columns('users')]
    existing_tables = inspector.get_table_names()

    # 1. Add is_superuser to users if not present
    if 'is_superuser' not in user_columns:
        op.add_column(
            'users',
            sa.Column('is_superuser', sa.Boolean(), server_default='false', nullable=False),
        )

    # 2. Create ai_usage_ledger table
    if 'ai_usage_ledger' not in existing_tables:
        op.create_table(
            'ai_usage_ledger',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('owner_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('content_job_id', sa.Integer(), nullable=True),
            sa.Column('provider', sa.String(length=50), nullable=False),
            sa.Column('model', sa.String(length=100), nullable=False),
            sa.Column('tokens_prompt', sa.Integer(), server_default='0', nullable=False),
            sa.Column('tokens_completion', sa.Integer(), server_default='0', nullable=False),
            sa.Column('cost_cents', sa.Integer(), server_default='0', nullable=False),
            sa.Column('operation_type', sa.String(length=50), nullable=False),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                server_default=sa.text('now()'),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['content_job_id'], ['content_jobs.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_ai_usage_ledger_owner_id'), 'ai_usage_ledger', ['owner_id'], unique=False)
        op.create_index(op.f('ix_ai_usage_ledger_user_id'), 'ai_usage_ledger', ['user_id'], unique=False)
        op.create_index(op.f('ix_ai_usage_ledger_content_job_id'), 'ai_usage_ledger', ['content_job_id'], unique=False)
        op.create_index(op.f('ix_ai_usage_ledger_created_at'), 'ai_usage_ledger', ['created_at'], unique=False)

    # 3. Create tickets table
    if 'tickets' not in existing_tables:
        op.create_table(
            'tickets',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('subject', sa.String(length=200), nullable=False),
            sa.Column('category', sa.String(length=50), server_default='general', nullable=False),
            sa.Column('priority', sa.String(length=20), server_default='medium', nullable=False),
            sa.Column('status', sa.String(length=20), server_default='open', nullable=False),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                server_default=sa.text('now()'),
                nullable=False,
            ),
            sa.Column(
                'updated_at',
                sa.DateTime(timezone=True),
                server_default=sa.text('now()'),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_tickets_user_id'), 'tickets', ['user_id'], unique=False)
        op.create_index(op.f('ix_tickets_status'), 'tickets', ['status'], unique=False)

    # 4. Create ticket_messages table
    if 'ticket_messages' not in existing_tables:
        op.create_table(
            'ticket_messages',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('ticket_id', sa.Integer(), nullable=False),
            sa.Column('sender_id', sa.Integer(), nullable=False),
            sa.Column('is_staff', sa.Boolean(), server_default='false', nullable=False),
            sa.Column('is_internal_note', sa.Boolean(), server_default='false', nullable=False),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('attachments', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                server_default=sa.text('now()'),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_ticket_messages_ticket_id'), 'ticket_messages', ['ticket_id'], unique=False)
        op.create_index(op.f('ix_ticket_messages_sender_id'), 'ticket_messages', ['sender_id'], unique=False)


def downgrade() -> None:
    """Downgrade tables and columns."""
    op.drop_index(op.f('ix_ticket_messages_sender_id'), table_name='ticket_messages')
    op.drop_index(op.f('ix_ticket_messages_ticket_id'), table_name='ticket_messages')
    op.drop_table('ticket_messages')

    op.drop_index(op.f('ix_tickets_status'), table_name='tickets')
    op.drop_index(op.f('ix_tickets_user_id'), table_name='tickets')
    op.drop_table('tickets')

    op.drop_index(op.f('ix_ai_usage_ledger_created_at'), table_name='ai_usage_ledger')
    op.drop_index(op.f('ix_ai_usage_ledger_content_job_id'), table_name='ai_usage_ledger')
    op.drop_index(op.f('ix_ai_usage_ledger_user_id'), table_name='ai_usage_ledger')
    op.drop_index(op.f('ix_ai_usage_ledger_owner_id'), table_name='ai_usage_ledger')
    op.drop_table('ai_usage_ledger')

    op.drop_column('users', 'is_superuser')
