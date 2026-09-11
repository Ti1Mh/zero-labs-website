"""create brand_personas table

Revision ID: c7a42b10de88
Revises: f9bfaac801d7
Create Date: 2026-09-11 05:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7a42b10de88'
down_revision: Union[str, Sequence[str], None] = 'f9bfaac801d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create brand_personas table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'brand_personas' not in existing_tables:
        op.create_table(
            'brand_personas',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('owner_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('tone_traits', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
            sa.Column('forbidden_words', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
            sa.Column('signature_phrases', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
            sa.Column('sample_posts', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
            sa.Column('target_audience', sa.String(length=300), nullable=True),
            sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
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
            sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_brand_personas_owner_id'), 'brand_personas', ['owner_id'], unique=False)


def downgrade() -> None:
    """Drop brand_personas table."""
    op.drop_index(op.f('ix_brand_personas_owner_id'), table_name='brand_personas')
    op.drop_table('brand_personas')
