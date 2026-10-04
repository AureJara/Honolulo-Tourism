"""Piezas compartidas por las pantallas de administración (lugares, fotos y moderación de opiniones)."""

from __future__ import annotations

import functools
import uuid

from flask import abort, g, redirect, request, url_for

from .pages import _body, current_user
from .upstream import UpstreamUnavailable, downstream

CATALOG_DOWN = "El catálogo de lugares no está disponible. Inténtalo nuevamente en unos minutos."
AUTH_DOWN = "El servicio de cuentas no está disponible. Inténtalo nuevamente en unos minutos."


def admin_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("pages.login", next=request.path))
        if user.get("role") != "admin":
            abort(403, description="No tienes permisos para administrar los lugares.")
        g.user = user
        return view(*args, **kwargs)
    return wrapper


def service_call(service: str, down_message: str, method: str, path: str, **kwargs):
    """Llama a un microservicio con la sesión de quien administra; si no responde, muestra la página de error 503."""
    try:
        return downstream(g.state, service, method, path, **kwargs)
    except UpstreamUnavailable:
        abort(503, description=down_message)


def catalog_call(method: str, path: str, **kwargs):
    return service_call("catalog", CATALOG_DOWN, method, path, **kwargs)


def auth_call(method: str, path: str, **kwargs):
    return service_call("auth", AUTH_DOWN, method, path, **kwargs)


def error_messages(resp) -> tuple[dict[str, str], str | None]:
    body = _body(resp)
    fields = {}
    for item in body.get("errors", []):
        fields.setdefault(item.get("field", "_"), item.get("message", ""))
    return fields, (None if fields else body.get("detail") or "No se pudo completar la operación.")


def uuid_or_404(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        abort(404)
