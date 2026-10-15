"""visitas diarias por lugar

Revision ID: c8d2e5f1a4b6
Revises: a3f1c9d2b7e4
Create Date: 2026-10-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c8d2e5f1a4b6'
down_revision = 'a3f1c9d2b7e4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('place_visits',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('place_id', sa.Uuid(), nullable=False),
    sa.Column('visit_date', sa.Date(), nullable=False),
    sa.Column('visitors', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=10), server_default='registro', nullable=False),
    sa.Column('recorded_by', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('visitors BETWEEN 0 AND 50000', name='ck_visits_range'),
    sa.CheckConstraint("source IN ('registro','demo')", name='ck_visits_source'),
    sa.ForeignKeyConstraint(['place_id'], ['places.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('place_id', 'visit_date', name='uq_visit_place_day')
    )
    with op.batch_alter_table('place_visits', schema=None) as batch_op:
        batch_op.create_index('ix_place_visits_date', ['visit_date'], unique=False)


def downgrade():
    with op.batch_alter_table('place_visits', schema=None) as batch_op:
        batch_op.drop_index('ix_place_visits_date')
    op.drop_table('place_visits')
