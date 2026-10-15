"""Pasarela de la boletería: el navegador solo habla con la web y esta reenvía al catálogo.

* ``GET /api/v1/tickets/config`` (el precio) es público: también lo ve el visitante sin sesión.
* Comprar, ver «mis boletos» y anular exigen sesión; validar en la puerta y ver las ventas son del administrador
  (el catálogo comprueba el rol). El precio y el total los calcula siempre el catálogo, nunca el navegador.
"""

from __future__ import annotations

from flask import Blueprint, Response, g, request

from honolulo_common.errors import ApiError

from .relay import relay
from .security import require_xhr_header
from .upstream import UpstreamUnavailable, anonymous, downstream

bp = Blueprint("bff_tickets", __name__)
UNAVAILABLE = "La boletería no está disponible. Inténtalo nuevamente."


def _need_session(mutating: bool) -> None:
    if mutating and not require_xhr_header():
        raise ApiError(403, "CSRF_REJECTED", "Solicitud no permitida.")
    if not g.state.access and not g.state.refresh:
        raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.")


def _catalog(method: str, path: str, *, json=None) -> Response:
    try:
        resp = downstream(g.state, "catalog", method, path, json=json)
    except UpstreamUnavailable:
        raise ApiError(503, "SERVICE_UNAVAILABLE", UNAVAILABLE) from None
    return relay(resp)


@bp.get("/api/v1/tickets/config")
def tickets_config():
    try:
        resp = anonymous("catalog", "GET", "/api/v1/tickets/config")
    except UpstreamUnavailable:
        raise ApiError(503, "SERVICE_UNAVAILABLE", UNAVAILABLE) from None
    return relay(resp)


@bp.post("/api/v1/tickets")
def tickets_buy():
    _need_session(True)
    return _catalog("POST", "/api/v1/tickets", json=request.get_json(silent=True))


@bp.get("/api/v1/tickets/mine")
def tickets_mine():
    _need_session(False)
    return _catalog("GET", "/api/v1/tickets/mine")


@bp.post("/api/v1/tickets/cancel")
def tickets_cancel():
    _need_session(True)
    return _catalog("POST", "/api/v1/tickets/cancel", json=request.get_json(silent=True))


@bp.post("/api/v1/admin/tickets/validate")
def tickets_validate():
    _need_session(True)
    return _catalog("POST", "/api/v1/admin/tickets/validate", json=request.get_json(silent=True))


@bp.get("/api/v1/admin/tickets/summary")
def tickets_summary():
    _need_session(False)
    return _catalog("GET", "/api/v1/admin/tickets/summary")
