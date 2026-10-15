"""boleteria: compras de boletos de entrada

Revision ID: e5a7b3c9d1f2
Revises: c8d2e5f1a4b6
Create Date: 2026-10-14 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e5a7b3c9d1f2'
down_revision = 'c8d2e5f1a4b6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ticket_orders',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=16), nullable=False),
    sa.Column('place_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('buyer_name', sa.String(length=100), nullable=False),
    sa.Column('visit_date', sa.Date(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_price_cents', sa.Integer(), nullable=False),
    sa.Column('total_cents', sa.Integer(), nullable=False),
    sa.Column('payment_method', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), server_default='pagado', nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('quantity BETWEEN 1 AND 20', name='ck_tickets_quantity'),
    sa.CheckConstraint("status IN ('pagado','usado','anulado')", name='ck_tickets_status'),
    sa.CheckConstraint("payment_method IN ('yape','plin','tarjeta','efectivo')", name='ck_tickets_method'),
    sa.CheckConstraint('unit_price_cents >= 0 AND total_cents = unit_price_cents * quantity', name='ck_tickets_total'),
    sa.ForeignKeyConstraint(['place_id'], ['places.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    with op.batch_alter_table('ticket_orders', schema=None) as batch_op:
        batch_op.create_index('ix_ticket_orders_user', ['user_id', 'created_at'], unique=False)
        batch_op.create_index('ix_ticket_orders_date', ['visit_date'], unique=False)


def downgrade():
    with op.batch_alter_table('ticket_orders', schema=None) as batch_op:
        batch_op.drop_index('ix_ticket_orders_date')
        batch_op.drop_index('ix_ticket_orders_user')
    op.drop_table('ticket_orders')
