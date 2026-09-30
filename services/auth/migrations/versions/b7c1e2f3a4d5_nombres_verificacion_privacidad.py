"""nombre y apellido, correo confirmado, consentimiento de privacidad y códigos de confirmación

Revision ID: b7c1e2f3a4d5
Revises: 494513510e2d
Create Date: 2026-09-30 17:00:00

Los usuarios existentes se conservan: su correo se considera ya confirmado (email_verified_at =
created_at) y el nombre completo se reparte en nombre y apellido.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7c1e2f3a4d5'
down_revision = '494513510e2d'
branch_labels = None
depends_on = None


def _canonical(email: str) -> str:
    email = email.strip().lower()
    local, _, domain = email.rpartition('@')
    if domain in ('gmail.com', 'googlemail.com'):
        local, domain = local.split('+', 1)[0].replace('.', ''), 'gmail.com'
    return f'{local}@{domain}'


def upgrade():
    op.add_column('users', sa.Column('first_name', sa.String(length=50), nullable=True))
    op.add_column('users', sa.Column('last_name', sa.String(length=50), nullable=True))
    op.add_column('users', sa.Column('email_canonical', sa.String(length=254), nullable=True))
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('privacy_accepted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('privacy_policy_version', sa.String(length=20), nullable=True))

    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE users SET first_name = split_part(btrim(full_name), ' ', 1), "
        "last_name = COALESCE(NULLIF(btrim(substr(btrim(full_name), length(split_part(btrim(full_name), ' ', 1)) + 1)), ''), '-'), "
        "email_verified_at = created_at"))
    for user_id, email in bind.execute(sa.text("SELECT id, email FROM users")).fetchall():
        bind.execute(sa.text("UPDATE users SET email_canonical = :c WHERE id = :i"), {"c": _canonical(email), "i": user_id})

    op.alter_column('users', 'first_name', nullable=False)
    op.alter_column('users', 'last_name', nullable=False)
    op.alter_column('users', 'email_canonical', nullable=False)
    op.create_unique_constraint('uq_users_email_canonical', 'users', ['email_canonical'])
    op.drop_column('users', 'full_name')

    op.create_table(
        'email_verifications',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('purpose', sa.String(length=20), server_default='signup', nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.SmallInteger(), server_default='0', nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_email_verifications_user_id', 'email_verifications', ['user_id'])


def downgrade():
    op.drop_index('ix_email_verifications_user_id', table_name='email_verifications')
    op.drop_table('email_verifications')
    op.add_column('users', sa.Column('full_name', sa.String(length=100), nullable=True))
    op.get_bind().execute(sa.text("UPDATE users SET full_name = btrim(first_name || ' ' || last_name)"))
    op.alter_column('users', 'full_name', nullable=False)
    op.drop_constraint('uq_users_email_canonical', 'users', type_='unique')
    for column in ('privacy_policy_version', 'privacy_accepted_at', 'email_verified_at',
                   'email_canonical', 'last_name', 'first_name'):
        op.drop_column('users', column)
