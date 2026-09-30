"""Emisión y rotación de tokens de sesión."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

import jwt
from flask import current_app

from honolulo_common.errors import ApiError
from honolulo_common.security import JWT_ALGORITHM
from honolulo_common.timeutil import as_utc, utcnow

from .extensions import db
from .models import RefreshToken, User


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user: User) -> str:
    cfg = current_app.config
    now = utcnow()
    claims = {
        "sub": str(user.id), "email": user.email, "name": user.full_name, "role": user.role,
        "first_name": user.first_name,
        "type": "access", "iss": cfg["JWT_ISSUER"], "aud": cfg["JWT_AUDIENCE"],
        "iat": now, "exp": now + timedelta(minutes=cfg["ACCESS_TOKEN_MINUTES"]),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, cfg["JWT_SECRET_KEY"], algorithm=JWT_ALGORITHM)


def _new_refresh_token(user: User, family_id: uuid.UUID, user_agent: str | None) -> tuple[str, RefreshToken]:
    raw = secrets.token_urlsafe(48)
    row = RefreshToken(
        user_id=user.id, family_id=family_id, token_hash=hash_token(raw),
        expires_at=utcnow() + timedelta(days=current_app.config["REFRESH_TOKEN_DAYS"]),
        user_agent=(user_agent or "")[:200] or None,
    )
    db.session.add(row)
    return raw, row


def issue_tokens(user: User, *, family_id: uuid.UUID | None = None, user_agent: str | None = None) -> dict:
    refresh_raw, row = _new_refresh_token(user, family_id or uuid.uuid4(), user_agent)
    db.session.flush()
    return {
        "access_token": create_access_token(user),
        "refresh_token": refresh_raw,
        "token_type": "Bearer",
        "expires_in": current_app.config["ACCESS_TOKEN_MINUTES"] * 60,
        "_refresh_row": row,
    }


def rotate_refresh_token(raw: str, user_agent: str | None) -> tuple[User, dict]:
    """Canjea un refresh token por un par nuevo. La reutilización revoca toda la familia."""
    invalid = ApiError(401, "INVALID_REFRESH_TOKEN", "La sesión no es válida. Inicia sesión nuevamente.")
    row = db.session.execute(
        db.select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise invalid
    now = utcnow()
    if row.revoked_at is not None:
        # Reutilización de un token ya rotado: se asume robo y se cierra toda la cadena.
        db.session.execute(
            db.update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.session.commit()
        raise invalid
    if as_utc(row.expires_at) <= now:
        raise invalid
    user = db.session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise invalid

    tokens = issue_tokens(user, family_id=row.family_id, user_agent=user_agent)
    row.revoked_at = now
    row.replaced_by = tokens["_refresh_row"].id
    return user, tokens


def revoke_refresh_token(raw: str) -> None:
    row = db.session.execute(
        db.select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()


def public_tokens(tokens: dict) -> dict:
    return {k: v for k, v in tokens.items() if not k.startswith("_")}
