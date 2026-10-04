"""Registro de eventos de seguridad: quién entró, quién falló, bloqueos, robos de sesión y cambios de rol.

Cada evento guarda la fecha, el tipo, la cuenta (si se conoce), la IP y, del correo, solo una pista parcial
(``a***@gmail.com``) y una huella con clave (HMAC) para poder agrupar intentos sobre un mismo correo sin guardarlo.
Nunca se guardan contraseñas, códigos ni tokens. Los eventos se conservan ``AUDIT_RETENTION_DAYS`` (``flask purge-audit``).

Añadir un tipo de evento es añadir un miembro de ``Event``; ``record`` y quienes lo consultan no cambian.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import ipaddress
import uuid
from datetime import timedelta

from flask import current_app, has_request_context, request

from honolulo_common.timeutil import utcnow

from .emails import canonical_email
from .extensions import db
from .models import AuditEvent, User


class Event(str, enum.Enum):
    REGISTERED = "registered"
    EMAIL_VERIFIED = "email_verified"
    VERIFY_FAILED = "verify_failed"
    CODE_RESENT = "code_resent"
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGIN_UNVERIFIED = "login_unverified"
    LOGIN_BLOCKED = "login_blocked"
    ACCOUNT_LOCKED = "account_locked"
    LOGOUT = "logout"
    REFRESH_REUSE = "refresh_reuse"            # un token de sesión ya usado volvió a presentarse: posible robo
    ADMIN_CREATED = "admin_created"
    ROLE_CHANGED = "role_changed"


def client_ip() -> str | None:
    """IP de quien hace la petición. La web la reenvía en ``X-Client-IP``: los servicios internos solo son accesibles
    desde ella (o desde la propia máquina en desarrollo), así que el valor se acepta si es una IP válida."""
    if not has_request_context():
        return None
    claimed = request.headers.get("X-Client-IP", "").strip()
    try:
        return str(ipaddress.ip_address(claimed))
    except ValueError:
        return request.remote_addr


def email_hint(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.strip().lower().partition("@")
    return f"{local[:1]}***@{domain[:100]}"


def fingerprint(email: str | None) -> str | None:
    if not email:
        return None
    key = current_app.config["JWT_SECRET_KEY"].encode()
    return hmac.new(key, b"audit-email:" + canonical_email(email).encode(), hashlib.sha256).hexdigest()[:16]


def record(event: Event, *, user: User | None = None, user_id: uuid.UUID | None = None, email: str | None = None,
           **detail) -> AuditEvent:
    """Añade el evento a la transacción en curso (quien llama hace el ``commit``)."""
    email = email or (user.email if user is not None else None)
    row = AuditEvent(event=event.value, user_id=user.id if user is not None else user_id, email_hint=email_hint(email),
                     email_hash=fingerprint(email), ip=client_ip(), detail={k: v for k, v in detail.items()} or None)
    db.session.add(row)
    return row


def serialize(row: AuditEvent) -> dict:
    return {"id": row.id, "occurred_at": row.occurred_at.isoformat(), "event": row.event,
            "user_id": str(row.user_id) if row.user_id else None, "email_hint": row.email_hint,
            "email_hash": row.email_hash, "ip": row.ip, "detail": row.detail or {}}


def page(limit: int, offset: int, event: str | None = None) -> tuple[list[dict], int]:
    query = db.select(AuditEvent)
    if event:
        query = query.where(AuditEvent.event == event)
    total = db.session.execute(db.select(db.func.count()).select_from(query.subquery())).scalar_one()
    rows = db.session.execute(query.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)).scalars()
    return [serialize(r) for r in rows], total


def purge_older_than(days: int) -> int:
    """Borra los eventos más antiguos que ``days`` días y devuelve cuántos fueron."""
    result = db.session.execute(db.delete(AuditEvent).where(AuditEvent.occurred_at < utcnow() - timedelta(days=days)))
    db.session.commit()
    return result.rowcount
