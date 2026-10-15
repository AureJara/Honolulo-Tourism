"""Pasarela de la afluencia de visitantes: el navegador solo habla con la web y esta reenvía al catálogo.

* ``GET /api/v1/visits/stats``: con sesión. Para la vista diaria añade la probabilidad de lluvia de los próximos días,
  tomada del pronóstico del servicio de clima, para que la predicción la tenga en cuenta (si el clima no responde, se
  devuelve la predicción sin ese ajuste).
* El registro de visitas y la limpieza de datos de demostración son del administrador (el catálogo comprueba el rol).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, g, request

from honolulo_common.errors import ApiError

from .relay import json_or_none, relay
from .security import require_xhr_header
from .upstream import UpstreamUnavailable, downstream
from .validators import slug_or_404

bp = Blueprint("bff_visits", __name__)
RAIN_DAYS = 7
OPEN_HOURS = range(8, 18)                       # horario de visita: 08:00-18:00


def _need_session(mutating: bool) -> None:
    if mutating and not require_xhr_header():
        raise ApiError(403, "CSRF_REJECTED", "Solicitud no permitida.")
    if not g.state.access and not g.state.refresh:
        raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.")


def _catalog(method: str, path: str, *, json=None, params=None) -> Response:
    try:
        resp = downstream(g.state, "catalog", method, path, json=json, params=params)
    except UpstreamUnavailable:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "El registro de visitas no está disponible. Inténtalo nuevamente.") from None
    return relay(resp)


def _rain_by_day(state) -> str:
    """``AAAA-MM-DD:porcentaje,...`` con la probabilidad media de lluvia en horario de visita; vacío si no hay pronóstico."""
    today = datetime.now(timezone(timedelta(hours=-5))).date()
    days = [today + timedelta(days=i) for i in range(RAIN_DAYS)]

    def one(day):
        try:
            resp = downstream(state, "forecast", "GET", "/api/v1/weather/forecast", params={"date": day.isoformat()})
        except UpstreamUnavailable:
            return None
        body = json_or_none(resp) if resp.status_code == 200 else None
        if not body or not body.get("available"):
            return None
        probs = [e["rain_probability"] for e in body.get("forecast", [])
                 if e.get("rain_probability") is not None and int(e["time"][:2]) in OPEN_HOURS]
        return round(sum(probs) / len(probs)) if probs else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(one, days))
    return ",".join(f"{d.isoformat()}:{v}" for d, v in zip(days, values) if v is not None)


@bp.get("/api/v1/visits/stats")
def visits_stats():
    _need_session(False)
    params = {k: request.args[k] for k in ("granularity",) if k in request.args}
    if request.args.get("place"):
        params["place"] = slug_or_404(request.args["place"])
    if params.get("granularity") == "day":
        rain = _rain_by_day(g.state)
        if rain:
            params["rain"] = rain
    return _catalog("GET", "/api/v1/visits/stats", params=params)


@bp.get("/api/v1/visits/regional")
def visits_regional():
    _need_session(False)
    return _catalog("GET", "/api/v1/visits/regional")


@bp.post("/api/v1/admin/visits")
def visits_record():
    _need_session(True)
    return _catalog("POST", "/api/v1/admin/visits", json=request.get_json(silent=True))


@bp.delete("/api/v1/admin/visits")
def visits_delete_record():
    _need_session(True)
    params = {k: request.args[k] for k in ("date",) if k in request.args}
    if request.args.get("place"):
        params["place"] = slug_or_404(request.args["place"])
    return _catalog("DELETE", "/api/v1/admin/visits", params=params)


@bp.get("/api/v1/admin/visits/recent")
def visits_recent():
    _need_session(False)
    return _catalog("GET", "/api/v1/admin/visits/recent")


@bp.delete("/api/v1/admin/visits/demo")
def visits_clear_demo():
    _need_session(True)
    params = {"place": slug_or_404(request.args["place"])} if request.args.get("place") else None
    return _catalog("DELETE", "/api/v1/admin/visits/demo", params=params)
