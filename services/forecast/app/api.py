from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from honolulo_common.errors import ApiError
from honolulo_common.security import auth_required
from honolulo_common.timeutil import parse_date, parse_month

from . import service

bp = Blueprint("forecast", __name__, url_prefix="/api/v1/weather")

_MIN_YEAR, _MAX_YEAR = 2020, 2100
_MAX_BIGINT = 2 ** 63 - 1


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _checked_date(field: str = "date"):
    day = parse_date(request.args.get(field), field)
    if not _MIN_YEAR <= day.year <= _MAX_YEAR:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": field, "message": f"El año debe estar entre {_MIN_YEAR} y {_MAX_YEAR}."}])
    return day


@bp.get("/forecast")
@auth_required()
def get_forecast():
    """RF05: pronóstico de un día (horario, franjas, métricas y evaluación)."""
    return _no_store(jsonify(service.day_forecast(_checked_date())))


@bp.get("/calendar")
@auth_required()
def get_calendar():
    """RF04: indicador climático de cada día del mes."""
    first, last = parse_month(request.args.get("month"))
    if not _MIN_YEAR <= first.year <= _MAX_YEAR:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": "month", "message": f"El año debe estar entre {_MIN_YEAR} y {_MAX_YEAR}."}])
    return _no_store(jsonify(service.calendar(first, last)))


@bp.post("/forecast/refresh")
@auth_required()
def refresh_forecast():
    """RF07: actualizar los pronósticos."""
    return _no_store(jsonify(service.manual_refresh()))


@bp.get("/evaluation/<int:forecast_id>")
@auth_required()
def get_evaluation(forecast_id: int):
    """RF10 / RF11: evaluación de un pronóstico concreto."""
    if forecast_id > _MAX_BIGINT:                      # no cabe en la columna: no puede existir (antes daba 500)
        raise ApiError(404, "NOT_FOUND", "No existe ese pronóstico.")
    return _no_store(jsonify(service.evaluate_forecast(forecast_id)))


@bp.get("/evaluation")
@auth_required()
def get_day_evaluation():
    """Precisión agregada de un día."""
    return _no_store(jsonify(service.evaluate_day(_checked_date())))


@bp.get("/scoring-parameters")
@auth_required()
def get_scoring_parameters():
    return _no_store(jsonify(service.serialize_params(service.active_params())))


@bp.put("/scoring-parameters")
@auth_required(roles=["admin"])
def put_scoring_parameters():
    """RF11: la fórmula de puntuación es un parámetro del sistema; cada cambio crea una versión."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(400, "BAD_REQUEST", "Se esperaba un cuerpo JSON.")
    row = service.create_params(payload, created_by=g.user["sub"])
    return jsonify(service.serialize_params(row)), 201
