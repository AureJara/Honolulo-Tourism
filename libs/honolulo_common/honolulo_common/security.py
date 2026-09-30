"""Verificación de JWT (HS256) y del token interno entre servicios."""

from __future__ import annotations

import functools
import hmac
from typing import Callable, Iterable

import jwt
from flask import current_app, g, request

from .errors import ApiError

JWT_ALGORITHM = "HS256"


def decode_access_token(token: str) -> dict:
    cfg = current_app.config
    try:
        claims = jwt.decode(
            token,
            cfg["JWT_SECRET_KEY"],
            algorithms=[JWT_ALGORITHM],
            audience=cfg["JWT_AUDIENCE"],
            issuer=cfg["JWT_ISSUER"],
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(401, "TOKEN_EXPIRED", "La sesión expiró. Inicia sesión nuevamente.") from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.") from exc
    if claims.get("type") != "access":
        raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.")
    return claims


def bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def auth_required(roles: Iterable[str] | None = None) -> Callable:
    """Exige una sesión activa (RF01). Opcionalmente restringe por rol.

    Deja las claims del token en ``g.user`` (``sub``, ``email``, ``name``, ``role``).
    """
    allowed = set(roles) if roles else None

    def decorator(view: Callable) -> Callable:
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            token = bearer_token()
            if not token:
                raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.")
            claims = decode_access_token(token)
            if allowed is not None and claims.get("role") not in allowed:
                raise ApiError(403, "FORBIDDEN", "No tienes permisos para esta operación.")
            g.user = claims
            return view(*args, **kwargs)

        return wrapper

    return decorator


def internal_required(view: Callable) -> Callable:
    """Protege endpoints ``/internal/*`` con el token compartido entre servicios."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        expected = current_app.config.get("INTERNAL_API_TOKEN") or ""
        provided = request.headers.get("X-Internal-Token", "")
        if not expected or not hmac.compare_digest(expected, provided):
            raise ApiError(403, "FORBIDDEN", "Acceso interno no autorizado.")
        return view(*args, **kwargs)

    return wrapper
