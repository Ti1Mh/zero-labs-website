"""create media_files table

Revision ID: f9bfaac801d7
Revises: d5d8fa8434e3
Create Date: 2026-09-11 04:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9bfaac801d7'
down_revision: Union[str, Sequence[str], None] = 'd5d8fa8434e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create media_files table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if 'media_files' not in existing_tables:
        op.create_table(
            'media_files',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('owner_id', sa.Integer(), nullable=False),
            sa.Column('file_key', sa.String(length=500), nullable=False),
            sa.Column('original_filename', sa.String(length=255), nullable=False),
            sa.Column('content_type', sa.String(length=100), nullable=False),
            sa.Column('file_size', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('bucket', sa.String(length=100), nullable=False),
            sa.Column('public_url', sa.Text(), nullable=False),
            sa.Column('content_job_id', sa.Integer(), nullable=True),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                server_default=sa.text('now()'),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['content_job_id'], ['content_jobs.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_media_files_owner_id'), 'media_files', ['owner_id'], unique=False)
        op.create_index(op.f('ix_media_files_file_key'), 'media_files', ['file_key'], unique=True)
        op.create_index(op.f('ix_media_files_content_job_id'), 'media_files', ['content_job_id'], unique=False)


def downgrade() -> None:
    """Drop media_files table."""
    op.drop_index(op.f('ix_media_files_content_job_id'), table_name='media_files')
    op.drop_index(op.f('ix_media_files_file_key'), table_name='media_files')
    op.drop_index(op.f('ix_media_files_owner_id'), table_name='media_files')
    op.drop_table('media_files')
