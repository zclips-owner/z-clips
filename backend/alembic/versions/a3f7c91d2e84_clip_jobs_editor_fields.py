"""clip_jobs: add YIZERGE-style editor fields

Revision ID: a3f7c91d2e84
Revises: 7f3a1c9d2e4b
Create Date: 2026-11-20

Adds aspect_ratio, layout, caption_size, caption_position, caption_bg,
sticker_text, post_author and post_text to clip_jobs. All default to the
plain-AI behaviour (9:16, standard layout, medium/bottom/outline captions,
no sticker or post card) so existing rows are unaffected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3f7c91d2e84'
down_revision: Union[str, None] = '7f3a1c9d2e4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clip_jobs', sa.Column('aspect_ratio', sa.String(length=8), server_default='9:16', nullable=False))
    op.add_column('clip_jobs', sa.Column('layout', sa.String(length=16), server_default='standard', nullable=False))
    op.add_column('clip_jobs', sa.Column('caption_size', sa.String(length=8), server_default='medium', nullable=False))
    op.add_column('clip_jobs', sa.Column('caption_position', sa.String(length=8), server_default='bottom', nullable=False))
    op.add_column('clip_jobs', sa.Column('caption_bg', sa.String(length=8), server_default='outline', nullable=False))
    op.add_column('clip_jobs', sa.Column('sticker_text', sa.String(length=40), server_default='', nullable=False))
    op.add_column('clip_jobs', sa.Column('post_author', sa.String(length=40), server_default='', nullable=False))
    op.add_column('clip_jobs', sa.Column('post_text', sa.String(length=140), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('clip_jobs', 'post_text')
    op.drop_column('clip_jobs', 'post_author')
    op.drop_column('clip_jobs', 'sticker_text')
    op.drop_column('clip_jobs', 'caption_bg')
    op.drop_column('clip_jobs', 'caption_position')
    op.drop_column('clip_jobs', 'caption_size')
    op.drop_column('clip_jobs', 'layout')
    op.drop_column('clip_jobs', 'aspect_ratio')
