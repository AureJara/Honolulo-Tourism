"""auditoria de seguridad

Revision ID: c4d8e1f5a9b2
Revises: b7c1e2f3a4d5
Create Date: 2026-10-03 22:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c4d8e1f5a9b2'
down_revision = 'b7c1e2f3a4d5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('audit_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('event', sa.String(length=40), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('email_hint', sa.String(length=120), nullable=True),
    sa.Column('email_hash', sa.String(length=16), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('detail', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index('ix_audit_events_time', ['occurred_at'], unique=False)
        batch_op.create_index('ix_audit_events_event_time', ['event', 'occurred_at'], unique=False)
        batch_op.create_index('ix_audit_events_user', ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_events_user')
        batch_op.drop_index('ix_audit_events_event_time')
        batch_op.drop_index('ix_audit_events_time')
    op.drop_table('audit_events')
