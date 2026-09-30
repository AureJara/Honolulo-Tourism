from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .extensions import db


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (sa.CheckConstraint("role IN ('user','admin')", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(sa.String(254), unique=True, nullable=False)
    # Forma canónica del correo (Gmail ignora puntos y «+etiqueta»): evita cuentas duplicadas.
    email_canonical: Mapped[str] = mapped_column(sa.String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    role: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="user", server_default="user")
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, server_default=sa.true())
    failed_attempts: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    privacy_policy_version: Mapped[str | None] = mapped_column(sa.String(20))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class EmailVerification(db.Model):
    """Código de confirmación del correo. Solo se guarda un HMAC del código, nunca el código."""

    __tablename__ = "email_verifications"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="signup", server_default="signup")
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False, default=0, server_default="0")
    consumed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


class RefreshToken(db.Model):
    """Refresh tokens rotatorios. Solo se guarda el hash SHA-256, nunca el token."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    user_agent: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
