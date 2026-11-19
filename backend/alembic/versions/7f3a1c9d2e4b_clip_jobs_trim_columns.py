"""clip_jobs: add trim_start_s and trim_end_s for editor re-renders

Revision ID: 7f3a1c9d2e4b
Revises: 468255d17319
Create Date: 2026-11-19

The clip editor creates a re-render ClipJob with a trim range. The range
is stored on the job (nullable; None means "use the full source") so the
worker can honour it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7f3a1c9d2e4b'
down_revision: Union[str, None] = '468255d17319'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clip_jobs', sa.Column('trim_start_s', sa.Float(), nullable=True))
    op.add_column('clip_jobs', sa.Column('trim_end_s', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('clip_jobs', 'trim_end_s')
    op.drop_column('clip_jobs', 'trim_start_s')
