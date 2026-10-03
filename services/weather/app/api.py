from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, jsonify, request

from honolulo_common.errors import ApiError
from honolulo_common.security import auth_required, internal_required
from honolulo_common.timeutil import iso_local, parse_date, parse_iso_datetime

from . import service
from .extensions import db
from .models import ProviderFetchLog

bp = Blueprint("weather", __name__)


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _int_arg(name: str, default: int, low: int, high: int) -> int:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not low <= value <= high:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": name, "message": f"Debe ser un entero entre {low} y {high}."}])
    return value


# ------------------------------------------------------------ API pública
@bp.get("/api/v1/weather/location")
@auth_required()
def get_location():
    """RF02: ubicación principal y coordenadas usadas en las consultas (RN02)."""
    return jsonify(service.location().as_dict())


@bp.get("/api/v1/weather/current")
@auth_required()
def get_current():
    """RF03 / RF06: estado actual con fecha y hora de obtención (RN04)."""
    return _no_store(jsonify(service.get_current()))


@bp.post("/api/v1/weather/refresh")
@auth_required()
def refresh():
    """RF07: «Actualizar clima»."""
    return _no_store(jsonify(service.manual_refresh()))


@bp.get("/api/v1/weather/history")
@auth_required()
def get_history():
    """RF08: historial de lecturas guardadas."""
    start = parse_date(request.args["from"], "from") if request.args.get("from") else None
    end = parse_date(request.args["to"], "to") if request.args.get("to") else None
    if start and end and end < start:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": "to", "message": "Debe ser igual o posterior a 'from'."}])
    source = request.args.get("source", "all")
    if source not in {"all", "current", "hourly"}:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": "source", "message": "Use all, current o hourly."}])
    return _no_store(jsonify(service.history(
        start=start, end=end, source=source,
        page=_int_arg("page", 1, 1, 100000), page_size=_int_arg("page_size", 20, 1, 100))))


@bp.get("/api/v1/weather/status")
@auth_required()
def get_status():
    """Estado del proveedor para diagnósticos y para la interfaz."""
    loc = service.location()
    last = db.session.execute(
        db.select(ProviderFetchLog).where(ProviderFetchLog.kind == "current")
        .order_by(ProviderFetchLog.fetched_at.desc()).limit(1)).scalar_one_or_none()
    latest = service.latest_current()
    return _no_store(jsonify({
        "location": loc.name, "provider": service.provider().name,
        "last_attempt_at": iso_local(last.fetched_at, loc.timezone) if last else None,
        "last_attempt_ok": last.ok if last else None,
        "last_update_at": iso_local(latest.fetched_at, loc.timezone) if latest else None,
    }))


# ------------------------------------------------------- API interna (servicios)
@bp.get("/internal/observations")
@internal_required
def internal_observations():
    """Serie horaria observada para que forecast-service evalúe pronósticos (RN06)."""
    start = parse_iso_datetime(request.args.get("from"), "from")
    end = parse_iso_datetime(request.args.get("to"), "to")
    if end < start or end - start > timedelta(days=10):
        raise ApiError(422, "VALIDATION_ERROR", "Rango inválido (máximo 10 días).")
    if request.args.get("ensure") == "1":
        rows = service.ensure_hourly(start, end)
    else:
        rows = service.hourly_observations(start, end)
    return _no_store(jsonify({"location": service.location().code,
                              "items": [service.serialize_internal(r) for r in rows]}))
