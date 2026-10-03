"""opiniones y puntuacion de los lugares

Revision ID: a3f1c9d2b7e4
Revises: 71d9d7eff8cd
Create Date: 2026-10-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a3f1c9d2b7e4'
down_revision = '71d9d7eff8cd'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('place_reviews',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('place_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('author_name', sa.String(length=60), nullable=False),
    sa.Column('rating', sa.SmallInteger(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('pinned', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('pinned_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('pinned_by', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(pinned AND pinned_at IS NOT NULL) OR (NOT pinned AND pinned_at IS NULL)', name='ck_reviews_pin_consistent'),
    sa.CheckConstraint('comment IS NULL OR char_length(comment) BETWEEN 3 AND 1000', name='ck_reviews_comment_len'),
    sa.CheckConstraint('rating BETWEEN 1 AND 5', name='ck_reviews_rating'),
    sa.ForeignKeyConstraint(['place_id'], ['places.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('place_id', 'user_id', name='uq_review_place_user')
    )
    with op.batch_alter_table('place_reviews', schema=None) as batch_op:
        batch_op.create_index('ix_place_reviews_listing', ['place_id', 'pinned', 'created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('place_reviews', schema=None) as batch_op:
        batch_op.drop_index('ix_place_reviews_listing')
    op.drop_table('place_reviews')
