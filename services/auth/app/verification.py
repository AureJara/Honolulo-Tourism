"""Códigos de confirmación de correo: emisión, límites y verificación."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from flask import current_app

from honolulo_common.timeutil import as_utc, utcnow

from .extensions import db
from .models import EmailVerification, User


def _hash(user_id, code: str) -> str:
    key = current_app.config["JWT_SECRET_KEY"].encode()
    return hmac.new(key, f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def issue_code(user: User, *, force: bool = False) -> str | None:
    """Crea un código nuevo e invalida el anterior. Devuelve ``None`` si se superó algún límite:
    espera mínima entre envíos o máximo de envíos por hora (evita usar el servicio para enviar spam)."""
    cfg = current_app.config
    now = utcnow()
    recent = db.session.execute(
        db.select(EmailVerification.created_at).where(EmailVerification.user_id == user.id)
        .order_by(EmailVerification.created_at.desc()).limit(cfg["VERIFICATION_MAX_PER_HOUR"])).scalars().all()
    if recent:
        if not force and now - as_utc(recent[0]) < timedelta(seconds=cfg["VERIFICATION_RESEND_SECONDS"]):
            return None
        last_hour = [r for r in recent if now - as_utc(r) < timedelta(hours=1)]
        if len(last_hour) >= cfg["VERIFICATION_MAX_PER_HOUR"]:
            return None

    db.session.execute(
        db.update(EmailVerification)
        .where(EmailVerification.user_id == user.id, EmailVerification.consumed_at.is_(None))
        .values(consumed_at=now))
    code = f"{secrets.randbelow(10 ** 6):06d}"
    db.session.add(EmailVerification(
        user_id=user.id, code_hash=_hash(user.id, code),
        expires_at=now + timedelta(minutes=cfg["VERIFICATION_CODE_TTL_MIN"])))
    return code


def check_code(user: User, code: str) -> bool:
    """Comprueba el código vigente. Cada intento fallido se cuenta; al agotarlos el código se invalida."""
    now = utcnow()
    row = db.session.execute(
        db.select(EmailVerification)
        .where(EmailVerification.user_id == user.id, EmailVerification.consumed_at.is_(None),
               EmailVerification.expires_at > now)
        .order_by(EmailVerification.created_at.desc()).limit(1).with_for_update()).scalar_one_or_none()
    if row is None:
        return False
    row.attempts += 1
    if not hmac.compare_digest(row.code_hash, _hash(user.id, code)):
        if row.attempts >= current_app.config["VERIFICATION_MAX_ATTEMPTS"]:
            row.consumed_at = now
        db.session.commit()            # el intento fallido debe persistir aunque se rechace la petición
        return False
    row.consumed_at = now
    user.email_verified_at = now
    return True
