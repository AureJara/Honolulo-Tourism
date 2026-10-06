from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from honolulo_common.errors import ApiError
from honolulo_common.pagination import page_args
from honolulo_common.security import auth_required
from honolulo_common.timeutil import as_utc, utcnow

from . import audit, mailer, verification
from .emails import canonical_email, normalize_email
from .extensions import db
from .models import User
from .names import clean_person_name
from .schemas import (LoginSchema, RegisterSchema, ResendSchema, TokenSchema, VerifyEmailSchema, load_json)
from .audit import Event
from .tokens import issue_tokens, public_tokens, revoke_refresh_token, rotate_refresh_token

log = logging.getLogger(__name__)
bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")

# Hash de relleno para igualar tiempos cuando el correo no existe (evita enumeración por latencia).
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def user_json(user: User) -> dict:
    return {
        "id": str(user.id), "email": user.email, "first_name": user.first_name, "last_name": user.last_name,
        "full_name": user.full_name, "role": user.role, "email_verified": user.email_verified_at is not None,
        "created_at": as_utc(user.created_at).isoformat() if user.created_at else None,
    }


def _find_by_email(email: str) -> User | None:
    return db.session.execute(
        db.select(User).where(User.email_canonical == canonical_email(email))).scalar_one_or_none()


def _send_code(user: User, code: str | None) -> bool:
    """Envía el correo con el código. Devuelve False si no se pudo entregar."""
    if code is None:
        return True
    try:
        mailer.send_verification_email(user.email, user.first_name, code)
        return True
    except mailer.MailError:
        return False


def _delivery_failed() -> ApiError:
    return ApiError(503, "EMAIL_DELIVERY_FAILED",
                    "No pudimos enviar el correo de confirmación. Inténtalo nuevamente en unos minutos.")


def _verification_payload(user: User) -> dict:
    cfg = current_app.config
    return {"status": "verification_required", "email": user.email,
            "expires_in": cfg["VERIFICATION_CODE_TTL_MIN"] * 60,
            "resend_after": cfg["VERIFICATION_RESEND_SECONDS"], "delivery": mailer.delivery_info()}


@bp.post("/register")
def register():
    """Registro (Escenarios 7 y 8): nombre y apellido, consentimiento y código por correo.
    La cuenta queda sin sesión hasta confirmar el correo."""
    data = load_json(RegisterSchema())
    cfg = current_app.config
    email = normalize_email(data["email"])
    user = _find_by_email(email)
    if user is not None and user.email_verified_at is not None:
        raise ApiError(409, "EMAIL_TAKEN", "Ya existe una cuenta con ese correo.")

    now = utcnow()
    fields = dict(first_name=clean_person_name(data["first_name"]), last_name=clean_person_name(data["last_name"]),
                  password_hash=generate_password_hash(data["password"], method=cfg["PASSWORD_HASH_METHOD"]),
                  privacy_accepted_at=now,
                  privacy_policy_version=cfg["PRIVACY_POLICY_VERSION"])
    if user is None:
        user = User(email=email, email_canonical=canonical_email(email), **fields)
        db.session.add(user)
    else:
        # Cuenta aún sin confirmar: quien controla el buzón la reclamará con el código, así que se
        # actualizan los datos en vez de bloquear a la persona que se equivocó al registrarse.
        for key, value in fields.items():
            setattr(user, key, value)
    try:
        db.session.flush()
    except IntegrityError as exc:                # carrera entre dos registros simultáneos
        db.session.rollback()
        raise ApiError(409, "EMAIL_TAKEN", "Ya existe una cuenta con ese correo.") from exc
    code = verification.issue_code(user)
    audit.record(Event.REGISTERED, user=user)
    db.session.commit()
    if not _send_code(user, code):
        raise _delivery_failed()
    return jsonify(_verification_payload(user)), 201


@bp.post("/verify-email")
def verify_email():
    data = load_json(VerifyEmailSchema())
    invalid = ApiError(400, "INVALID_CODE", "Código incorrecto o vencido. Solicita uno nuevo si hace falta.")
    user = _find_by_email(data["email"])
    if user is None or user.email_verified_at is not None or not user.is_active:
        audit.record(Event.VERIFY_FAILED, user=user, email=data["email"], reason="no_pending_account")
        db.session.commit()
        raise invalid
    if not verification.check_code(user, data["code"]):
        audit.record(Event.VERIFY_FAILED, user=user, reason="wrong_or_expired_code")
        db.session.commit()
        raise invalid
    user.last_login_at = utcnow()
    audit.record(Event.EMAIL_VERIFIED, user=user)
    tokens = issue_tokens(user, user_agent=request.headers.get("User-Agent"))
    db.session.commit()
    return jsonify({**public_tokens(tokens), "user": user_json(user)})


@bp.post("/resend-code")
def resend_code():
    """Siempre responde 202 para no revelar si el correo está registrado."""
    data = load_json(ResendSchema())
    user = _find_by_email(data["email"])
    if user is not None and user.email_verified_at is None and user.is_active:
        code = verification.issue_code(user)
        audit.record(Event.CODE_RESENT, user=user, sent=code is not None)
        db.session.commit()
        if not _send_code(user, code):
            log.error("No se pudo reenviar el código de confirmación")
    return jsonify({"status": "accepted", "resend_after": current_app.config["VERIFICATION_RESEND_SECONDS"],
                    "delivery": mailer.delivery_info()}), 202


@bp.post("/login")
def login():
    data = load_json(LoginSchema())
    cfg = current_app.config
    user = _find_by_email(data["email"])
    now = utcnow()

    if user is not None and user.locked_until and as_utc(user.locked_until) > now:
        wait = int((as_utc(user.locked_until) - now).total_seconds())
        audit.record(Event.LOGIN_BLOCKED, user=user, wait_s=max(wait, 1))
        db.session.commit()
        raise ApiError(429, "ACCOUNT_LOCKED",
                       "Demasiados intentos fallidos. Inténtalo de nuevo más tarde.",
                       headers={"Retry-After": str(max(wait, 1))})

    valid = check_password_hash(user.password_hash if user else _DUMMY_HASH, data["password"])
    if user is None or not valid or not user.is_active:
        locked_now = False
        if user is not None and user.is_active:
            user.failed_attempts += 1
            if user.failed_attempts >= cfg["MAX_FAILED_ATTEMPTS"]:
                user.locked_until = now + timedelta(minutes=cfg["LOCKOUT_MINUTES"])
                user.failed_attempts = 0
                locked_now = True
        audit.record(Event.LOGIN_FAILED, user=user, email=data["email"],
                     reason="inactive_account" if user is not None and not user.is_active else "bad_credentials")
        if locked_now:
            audit.record(Event.ACCOUNT_LOCKED, user=user, minutes=cfg["LOCKOUT_MINUTES"])
        db.session.commit()
        raise ApiError(401, "INVALID_CREDENTIALS", "Correo o contraseña incorrectos.")

    if user.email_verified_at is None:
        # Contraseña correcta pero correo sin confirmar: se envía un código nuevo (respetando los límites).
        code = verification.issue_code(user)
        audit.record(Event.LOGIN_UNVERIFIED, user=user)
        db.session.commit()
        _send_code(user, code)
        raise ApiError(403, "EMAIL_NOT_VERIFIED", "Confirma tu correo con el código que te enviamos.",
                       extra={"email": user.email, "resend_after": cfg["VERIFICATION_RESEND_SECONDS"],
                              "delivery": mailer.delivery_info()})

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    audit.record(Event.LOGIN_SUCCESS, user=user)
    tokens = issue_tokens(user, user_agent=request.headers.get("User-Agent"))
    db.session.commit()
    return jsonify({**public_tokens(tokens), "user": user_json(user)})


@bp.post("/refresh")
def refresh():
    data = load_json(TokenSchema())
    user, tokens = rotate_refresh_token(data["refresh_token"], request.headers.get("User-Agent"))
    db.session.commit()
    return jsonify({**public_tokens(tokens), "user": user_json(user)})


@bp.post("/logout")
def logout():
    data = load_json(TokenSchema())
    user_id = revoke_refresh_token(data["refresh_token"])
    if user_id is not None:
        audit.record(Event.LOGOUT, user_id=user_id)
    db.session.commit()
    return "", 204


@bp.get("/me")
@auth_required()
def me():
    user = db.session.get(User, uuid.UUID(g.user["sub"]))
    if user is None or not user.is_active or user.email_verified_at is None:
        raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.")
    return jsonify(user_json(user))


@bp.get("/admin/audit")
@auth_required(roles=["admin"])
def audit_list():
    """Eventos de seguridad recientes (solo administradores): ``?event=login_failed&limit=50&offset=0``."""
    limit, offset = page_args(default_limit=50, max_limit=200)
    event = request.args.get("event") or None
    if event is not None and event not in {e.value for e in Event}:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los parámetros.",
                       errors=[{"field": "event", "message": "Tipo de evento desconocido."}])
    items, total = audit.page(limit, offset, event)
    resp = jsonify({"items": items, "total": total, "events": [e.value for e in Event]})
    resp.headers["Cache-Control"] = "no-store"
    return resp
