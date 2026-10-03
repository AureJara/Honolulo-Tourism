"""Piezas compartidas por las pantallas de administración (lugares, fotos y moderación de opiniones)."""

from __future__ import annotations

import functools
import uuid

from flask import abort, g, redirect, request, url_for

from .pages import _body, current_user
from .upstream import UpstreamUnavailable, downstream

CATALOG_DOWN = "El catálogo de lugares no está disponible. Inténtalo nuevamente en unos minutos."


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


def catalog_call(method: str, path: str, **kwargs):
    try:
        return downstream(g.state, "catalog", method, path, **kwargs)
    except UpstreamUnavailable:
        abort(503, description=CATALOG_DOWN)


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
