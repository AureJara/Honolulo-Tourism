"""Cookies de sesión y protección CSRF de la pasarela web."""

from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlparse

from flask import abort, current_app, g, request


def set_session_cookies(resp, tokens: dict) -> None:
    cfg = current_app.config
    common = dict(httponly=True, secure=cfg["COOKIE_SECURE"], samesite="Lax", path="/")
    resp.set_cookie(cfg["ACCESS_COOKIE"], tokens["access_token"], max_age=int(tokens["expires_in"]), **common)
    resp.set_cookie(cfg["REFRESH_COOKIE"], tokens["refresh_token"], max_age=cfg["REFRESH_COOKIE_MAX_AGE"], **common)


def clear_session_cookies(resp) -> None:
    cfg = current_app.config
    for name in (cfg["ACCESS_COOKIE"], cfg["REFRESH_COOKIE"]):
        resp.delete_cookie(name, path="/")


def apply_session_changes(resp):
    """``after_request``: propaga al navegador los tokens renovados o el cierre de sesión."""
    state = getattr(g, "state", None)
    if state is not None:
        if state.rotated:
            set_session_cookies(resp, state.rotated)
        elif state.cleared:
            clear_session_cookies(resp)
    return resp


# ------------------------------------------------------------------ CSRF
def csrf_token() -> str:
    """Token de doble envío: cookie + campo oculto del formulario. Se emite una vez por navegador."""
    token = request.cookies.get(current_app.config["CSRF_COOKIE"])
    if not token:
        token = secrets.token_urlsafe(32)
        g.new_csrf = token
    return token


def issue_csrf_cookie(resp):
    token = getattr(g, "new_csrf", None)
    if token:
        resp.set_cookie(current_app.config["CSRF_COOKIE"], token, httponly=True, samesite="Lax",
                        secure=current_app.config["COOKIE_SECURE"], path="/")
    return resp


def verify_csrf_form() -> None:
    cookie = request.cookies.get(current_app.config["CSRF_COOKIE"], "")
    field = request.form.get("csrf_token", "")
    if not cookie or not field or not hmac.compare_digest(cookie, field):
        abort(400, description="Formulario inválido o expirado. Recarga la página e inténtalo de nuevo.")


def require_xhr_header() -> bool:
    """Las mutaciones por API exigen una cabecera que un sitio externo no puede enviar sin CORS."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def safe_next(target: str | None) -> str:
    """Solo rutas internas (evita redirecciones abiertas tras el login)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    parsed = urlparse(target)
    return target if not parsed.scheme and not parsed.netloc else "/"
