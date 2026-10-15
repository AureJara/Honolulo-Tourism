"""Rutas HTTP de las visitas: estadísticas y predicción (con sesión), registro y limpieza (administrador)."""

from __future__ import annotations

import re
from datetime import date

from flask import Blueprint, g, jsonify, request
from marshmallow import fields, validate

from honolulo_common.errors import ApiError

from . import visits
from .permissions import admin_only, signed_in
from .responses import no_store
from .schemas import BaseSchema, load_body

bp = Blueprint("visits", __name__)
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class VisitSchema(BaseSchema):
    place = fields.String(required=True, validate=validate.Regexp(SLUG_RE))
    date = fields.Date(required=True)
    visitors = fields.Integer(required=True, strict=True, validate=validate.Range(min=0, max=50000))


def _slug_arg() -> str | None:
    slug = request.args.get("place") or None
    if slug and not (len(slug) <= 80 and SLUG_RE.match(slug)):
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los parámetros.",
                       errors=[{"field": "place", "message": "Lugar no válido."}])
    return slug


def _rain_arg() -> dict[date, float]:
    """``rain=2026-10-14:60,2026-10-15:20``: probabilidad de lluvia (%) por día, del pronóstico del tiempo."""
    out: dict[date, float] = {}
    raw = request.args.get("rain", "")
    for part in raw.split(",")[:31]:
        if not part:
            continue
        try:
            day, prob = part.split(":")
            out[date.fromisoformat(day)] = min(max(float(prob), 0.0), 100.0)
        except ValueError:
            raise ApiError(422, "VALIDATION_ERROR", "Revisa los parámetros.",
                           errors=[{"field": "rain", "message": "Formato esperado: AAAA-MM-DD:porcentaje."}]) from None
    return out


@bp.get("/api/v1/visits/stats")
@signed_in
def stats():
    data = visits.stats(request.args.get("granularity", "month"), _slug_arg(), _rain_arg())
    return no_store(jsonify(data))


@bp.get("/api/v1/visits/regional")
@signed_in
def regional():
    return no_store(jsonify(visits.regional_context()))


@bp.post("/api/v1/admin/visits")
@admin_only
def admin_record():
    data = load_body(VisitSchema())
    return no_store(jsonify(visits.record(data["place"], data["date"], data["visitors"], g.user["sub"]))), 201


@bp.delete("/api/v1/admin/visits")
@admin_only
def admin_delete_record():
    slug = _slug_arg()
    try:
        day = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los parámetros.",
                       errors=[{"field": "date", "message": "Fecha no válida (AAAA-MM-DD)."}]) from None
    if not slug:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los parámetros.",
                       errors=[{"field": "place", "message": "Indica el lugar."}])
    visits.delete_record(slug, day)
    return no_store(jsonify({"deleted": 1}))


@bp.get("/api/v1/admin/visits/recent")
@admin_only
def admin_recent():
    return no_store(jsonify({"items": visits.recent_records()}))


@bp.delete("/api/v1/admin/visits/demo")
@admin_only
def admin_delete_demo():
    return no_store(jsonify({"deleted": visits.delete_demo(_slug_arg())}))
