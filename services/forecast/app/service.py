"""Pronósticos: ingesta, selección del pronóstico de referencia, panel del día, calendario y
evaluación contra lo observado (RF04, RF05, RF09-RF11)."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy.dialects.postgresql import insert as pg_insert

from honolulo_common.conditions import condition_code_from_wmo, describe
from honolulo_common.errors import ApiError
from honolulo_common.location import Location
from honolulo_common.openmeteo import PROVIDER_NAME, ProviderError
from honolulo_common.timeutil import as_utc, iso_local, local_hour_to_utc, utcnow

from . import observations
from .domain import scoring
from .domain.daily import OPERATING_END, OPERATING_START, HourPoint, summarize_day
from .extensions import db
from .models import ForecastEvaluation, ProviderFetchLog, ScoringParameters, WeatherForecast

log = logging.getLogger(__name__)

# Mensajes exigidos por el spec.
RN07_MESSAGE = "Pronóstico pendiente de evaluación."
RN08_MESSAGE = "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."


def location() -> Location:
    return current_app.extensions["location"]


def provider():
    return current_app.extensions["weather_client"]


# ------------------------------------------------------ parámetros de puntuación
def active_params() -> ScoringParameters:
    row = db.session.execute(db.select(ScoringParameters).where(ScoringParameters.is_active)).scalar_one_or_none()
    if row is None:
        row = ScoringParameters(is_active=True, created_by="system", **_defaults_for_model())
        db.session.add(row)
        db.session.commit()
    return row


def _defaults_for_model() -> dict:
    d = scoring.DEFAULT_PARAMS
    return {"weights": d["weights"], "tolerances": d["tolerances"],
            "rain_prob_threshold_pct": d["rain_prob_threshold_pct"],
            "rain_observed_threshold_mm": d["rain_observed_threshold_mm"], "labels": d["labels"]}


def params_dict(row: ScoringParameters) -> dict:
    return {"weights": row.weights, "tolerances": row.tolerances,
            "rain_prob_threshold_pct": row.rain_prob_threshold_pct,
            "rain_observed_threshold_mm": float(row.rain_observed_threshold_mm), "labels": row.labels}


def serialize_params(row: ScoringParameters) -> dict:
    return {"version": row.version, "is_active": row.is_active, **params_dict(row),
            "created_by": row.created_by,
            "created_at": iso_local(row.created_at, location().timezone) if row.created_at else None}


def create_params(data: dict, created_by: str) -> ScoringParameters:
    """Publica una nueva versión de la fórmula; las evaluaciones anteriores conservan su versión."""
    try:
        clean = scoring.validate_params(data)
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_ERROR", "Parámetros de puntuación inválidos.",
                       errors=[{"field": "params", "message": str(exc)}]) from exc
    db.session.execute(db.update(ScoringParameters).where(ScoringParameters.is_active).values(is_active=False))
    row = ScoringParameters(is_active=True, created_by=created_by, **clean)
    db.session.add(row)
    db.session.commit()
    return row


# ------------------------------------------------------------------ ingesta
def last_issue_at() -> datetime | None:
    value = db.session.execute(
        db.select(db.func.max(WeatherForecast.issued_at)).where(WeatherForecast.location_code == location().code)
    ).scalar_one()
    return as_utc(value) if value else None


def _last_attempt_at() -> datetime | None:
    row = db.session.execute(
        db.select(ProviderFetchLog.fetched_at).order_by(ProviderFetchLog.fetched_at.desc()).limit(1)).first()
    return as_utc(row[0]) if row else None


def _log_fetch(ok: bool, error: str | None = None) -> None:
    stats = getattr(provider(), "last_stats", None)
    db.session.add(ProviderFetchLog(
        provider=PROVIDER_NAME, ok=ok, http_status=getattr(stats, "http_status", None),
        latency_ms=getattr(stats, "latency_ms", None), error=(error or "")[:300] or None, fetched_at=utcnow()))
    db.session.commit()


def refresh_forecast() -> int:
    """Pide el pronóstico horario al proveedor y lo guarda como una emisión nueva (RF09, RN05)."""
    loc, cfg = location(), current_app.config
    issued_at = utcnow().replace(second=0, microsecond=0)
    try:
        points = provider().fetch_hourly(loc.latitude, loc.longitude, loc.timezone,
                                         forecast_days=cfg["FORECAST_DAYS"], past_days=0)
    except ProviderError as exc:
        log.warning("Fallo al consultar el pronóstico: %s", exc)
        _log_fetch(False, str(exc))
        raise
    _log_fetch(True)
    # Solo horas futuras: lo que ya pasó no es un pronóstico, y nunca se inventan datos (RN03).
    rows = [{
        "location_code": loc.code, "issued_at": issued_at, "target_at": p.time_utc,
        "temperature_c": p.temperature_c, "feels_like_c": p.feels_like_c, "humidity_pct": p.humidity_pct,
        "precipitation_mm": p.precipitation_mm or 0, "rain_probability_pct": p.rain_probability_pct,
        "wind_kph": p.wind_kph, "cloud_cover_pct": p.cloud_cover_pct, "weather_code": p.weather_code,
        "condition": condition_code_from_wmo(p.weather_code), "provider": PROVIDER_NAME,
    } for p in points if p.time_utc > issued_at and p.temperature_c is not None]
    if rows:
        stmt = pg_insert(WeatherForecast).values(rows).on_conflict_do_nothing(constraint="uq_forecast_issue_target")
        db.session.execute(stmt)
        db.session.commit()
    return len(rows)


def manual_refresh() -> dict:
    """RF07: actualizar pronósticos, con intervalo mínimo global entre consultas al proveedor."""
    cfg, tz = current_app.config, location().timezone
    now = utcnow()
    interval = timedelta(seconds=cfg["REFRESH_MIN_INTERVAL_S"])
    attempt = _last_attempt_at()
    issued = last_issue_at()
    if attempt and now - attempt < interval:
        if issued and issued >= attempt - timedelta(minutes=1):
            return {"refreshed": False, "reason": "recent", "issued_at": iso_local(issued, tz),
                    "next_allowed_at": iso_local(attempt + interval, tz)}
        raise ApiError(502, "WEATHER_PROVIDER_UNAVAILABLE", RN08_MESSAGE,
                       extra={"last_issued_at": iso_local(issued, tz)} if issued else None)
    try:
        rows = refresh_forecast()
    except ProviderError:
        raise ApiError(502, "WEATHER_PROVIDER_UNAVAILABLE", RN08_MESSAGE,
                       extra={"last_issued_at": iso_local(issued, tz)} if issued else None) from None
    return {"refreshed": True, "rows": rows, "issued_at": iso_local(last_issue_at(), tz),
            "next_allowed_at": iso_local(utcnow() + interval, tz)}


def purge_old() -> int:
    cutoff = utcnow() - timedelta(days=current_app.config["FORECAST_RETENTION_DAYS"])
    result = db.session.execute(db.delete(WeatherForecast).where(WeatherForecast.target_at < cutoff))
    db.session.commit()
    return result.rowcount or 0


# ------------------------------------------------------- pronóstico de referencia
def _latest_per_target(conditions: list) -> list[WeatherForecast]:
    return list(db.session.execute(
        db.select(WeatherForecast).distinct(WeatherForecast.target_at)
        .where(WeatherForecast.location_code == location().code, *conditions)
        .order_by(WeatherForecast.target_at, WeatherForecast.issued_at.desc())).scalars())


def reference_forecasts(start: datetime, end: datetime, now: datetime | None = None) -> dict[datetime, WeatherForecast]:
    """Para cada hora objetivo de ``[start, end)`` elige el pronóstico que se muestra y se evalúa.

    * Horas futuras: el último pronóstico emitido.
    * Horas pasadas: el último emitido con al menos ``EVAL_LEAD_HOURS`` de antelación (así la
      evaluación no «mira atrás»); si no existe, el último emitido antes de la hora.
    """
    now = now or utcnow()
    lead = timedelta(hours=current_app.config["EVAL_LEAD_HOURS"])
    T, I = WeatherForecast.target_at, WeatherForecast.issued_at
    window = [T >= start, T < end]
    chosen: dict[datetime, WeatherForecast] = {}
    if end > now:
        for fc in _latest_per_target(window + [T >= now, I <= now]):
            chosen[as_utc(fc.target_at)] = fc
    if start < now:
        past = window + [T < now]
        for fc in _latest_per_target(past + [I + lead <= T]):
            chosen[as_utc(fc.target_at)] = fc
        for fc in _latest_per_target(past + [I <= T]):
            chosen.setdefault(as_utc(fc.target_at), fc)
    return chosen


def _point(fc: WeatherForecast, tz: str) -> HourPoint:
    target = as_utc(fc.target_at)
    return HourPoint(
        hour=target.astimezone(ZoneInfo(tz)).hour, temperature=fc.temperature_c, condition=fc.condition,
        humidity=fc.humidity_pct, rain_probability=fc.rain_probability_pct, precipitation=fc.precipitation_mm,
        wind_speed=fc.wind_kph, cloud_cover=fc.cloud_cover_pct, feels_like=fc.feels_like_c,
        forecast_id=fc.id, lead_hours=round((target - as_utc(fc.issued_at)).total_seconds() / 3600, 1))


def _forecast_dict(fc: WeatherForecast) -> dict:
    return {"temperature": fc.temperature_c, "humidity": fc.humidity_pct, "rain_probability": fc.rain_probability_pct,
            "precipitation": fc.precipitation_mm, "wind_speed": fc.wind_kph, "condition_code": fc.condition}


# -------------------------------------------------------------- panel del día
def _evaluations_for(ids: list[int], version: int) -> dict[int, ForecastEvaluation]:
    if not ids:
        return {}
    rows = db.session.execute(db.select(ForecastEvaluation).where(
        ForecastEvaluation.forecast_id.in_(ids), ForecastEvaluation.params_version == version)).scalars()
    return {r.forecast_id: r for r in rows}


def _day_hours(day: date) -> list[datetime]:
    tz = location().timezone
    return [local_hour_to_utc(day, h, tz) for h in range(OPERATING_START, OPERATING_END)]


def _day_references(day: date) -> list[WeatherForecast]:
    hours = _day_hours(day)
    refs = reference_forecasts(hours[0], hours[-1] + timedelta(hours=1))
    return [refs[h] for h in hours if h in refs]


def _day_evaluation_summary(chosen: list[WeatherForecast], evals: dict[int, ForecastEvaluation],
                            params: ScoringParameters) -> dict:
    total = len(chosen)
    done = [evals[fc.id] for fc in chosen if fc.id in evals]
    if not done:
        return {"status": "pending", "accuracy_score": None, "message": RN07_MESSAGE,
                "hours_evaluated": 0, "hours_total": total}
    score = round(sum(float(e.accuracy_score) for e in done) / len(done), 1)
    return {"status": "evaluated" if len(done) == total else "partial", "accuracy_score": score,
            "accuracy_label": scoring.label_for(score, params.labels),
            "hours_evaluated": len(done), "hours_total": total}


def day_forecast(day: date) -> dict:
    """Contrato de ``GET /api/v1/weather/forecast?date=`` (RF05) con los datos del panel del diseño."""
    loc = location()
    base = {"location": loc.name, "date": day.isoformat()}
    chosen = _day_references(day)
    if not chosen:
        future = day > utcnow().astimezone(ZoneInfo(loc.timezone)).date()
        return {**base, "available": False, "forecast": [],
                "message": ("Aún no hay pronóstico disponible para esta fecha." if future
                            else "No se guardó pronóstico para esta fecha.")}

    points = [_point(fc, loc.timezone) for fc in chosen]
    summary = summarize_day(points)
    params = active_params()
    evals = _evaluations_for([fc.id for fc in chosen], params.version)

    entries = []
    for fc, point in zip(chosen, points):
        cond = describe(fc.condition)
        ev = evals.get(fc.id)
        entries.append({
            "forecast_id": fc.id, "time": f"{point.hour:02d}:00",
            "temperature": fc.temperature_c, "feels_like": fc.feels_like_c, "humidity": fc.humidity_pct,
            "rain_probability": fc.rain_probability_pct, "precipitation": fc.precipitation_mm,
            "wind_speed": fc.wind_kph, "cloud_cover": fc.cloud_cover_pct,
            "condition": cond.text, "condition_code": fc.condition, "icon": cond.icon,
            "lead_hours": point.lead_hours,
            "evaluation": ({"status": "evaluated", "accuracy_score": float(ev.accuracy_score),
                            "accuracy_label": ev.accuracy_label} if ev else {"status": "pending"}),
        })
    issued = max(as_utc(fc.issued_at) for fc in chosen)
    return {**base, "available": True, "issued_at": iso_local(issued, loc.timezone),
            **summary, "forecast": entries, "evaluation": _day_evaluation_summary(chosen, evals, params)}


# ----------------------------------------------------------------- calendario
def calendar(first: date, last: date) -> dict:
    """Contrato de ``GET /api/v1/weather/calendar?month=`` (RF04): un indicador por día."""
    loc = location()
    zone = ZoneInfo(loc.timezone)
    refs = reference_forecasts(local_hour_to_utc(first, 0, loc.timezone),
                               local_hour_to_utc(last + timedelta(days=1), 0, loc.timezone))
    by_day: dict[date, list[HourPoint]] = defaultdict(list)
    newest = None
    for target, fc in refs.items():
        by_day[target.astimezone(zone).date()].append(_point(fc, loc.timezone))
        issued = as_utc(fc.issued_at)
        newest = issued if newest is None or issued > newest else newest

    days, cursor = [], first
    while cursor <= last:
        summary = summarize_day(by_day.get(cursor, []))
        item = {"date": cursor.isoformat(), "available": summary is not None}
        if summary:
            item.update({k: summary[k] for k in ("temperature", "condition", "condition_code", "icon")})
        days.append(item)
        cursor += timedelta(days=1)
    return {"location": loc.name, "month": first.strftime("%Y-%m"), "timezone": loc.timezone,
            "updated_at": iso_local(newest, loc.timezone), "days": days}


# ----------------------------------------------------------------- evaluación
def _persist_evaluations(rows: list[dict]) -> None:
    if rows:
        stmt = pg_insert(ForecastEvaluation).values(rows).on_conflict_do_nothing(constraint="uq_eval_forecast_params")
        db.session.execute(stmt)
        db.session.commit()


def evaluate_batch(forecasts: list[WeatherForecast]) -> int:
    """Evalúa pronósticos cuya hora ya pasó. Lanza ``ObservationsUnavailable`` si no hay datos observados."""
    if not forecasts:
        return 0
    start = min(as_utc(f.target_at) for f in forecasts)
    end = max(as_utc(f.target_at) for f in forecasts)
    observed = observations.fetch_hourly_observations(start, end)
    params_row = active_params()
    params = params_dict(params_row)
    rows = []
    for fc in forecasts:
        obs = observed.get(as_utc(fc.target_at))          # RN06: misma ubicación, fecha y hora
        if obs is None:
            continue
        try:
            result = scoring.evaluate(_forecast_dict(fc), obs, params)
        except scoring.NotEvaluable:
            continue
        rows.append({"forecast_id": fc.id, "observation_id": obs.get("id"), "params_version": params_row.version,
                     "accuracy_score": result.accuracy_score, "accuracy_label": result.accuracy_label,
                     "variables": result.variables, "evaluated_at": utcnow()})
    _persist_evaluations(rows)
    return len(rows)


def _evaluable_before() -> datetime:
    return utcnow() - timedelta(hours=current_app.config["EVAL_GRACE_HOURS"])


def serialize_evaluation(fc: WeatherForecast, ev: ForecastEvaluation | None, *, reason: str | None = None) -> dict:
    loc = location()
    base = {"location": loc.name, "forecast_id": fc.id,
            "target_at": iso_local(fc.target_at, loc.timezone), "issued_at": iso_local(fc.issued_at, loc.timezone)}
    if ev is None:
        body = {**base, "status": "pending", "accuracy_score": None, "message": RN07_MESSAGE}
        if reason:
            body["reason"] = reason
        return body
    return {**base, "status": "evaluated", "accuracy_score": float(ev.accuracy_score),
            "accuracy_label": ev.accuracy_label, "params_version": ev.params_version,
            "evaluated_at": iso_local(ev.evaluated_at, loc.timezone), "variables": ev.variables}


def evaluate_forecast(forecast_id: int) -> dict:
    """Contrato de ``GET /api/v1/weather/evaluation/{forecast_id}`` (RF10 / RF11)."""
    fc = db.session.get(WeatherForecast, forecast_id)
    if fc is None:
        raise ApiError(404, "NOT_FOUND", "No existe ese pronóstico.")
    version = active_params().version
    ev = _evaluations_for([fc.id], version).get(fc.id)
    reason = None
    if ev is None:
        if as_utc(fc.target_at) > _evaluable_before():
            reason = "target_in_future"                    # RN06: todavía no hay observación
        else:
            try:
                evaluate_batch([fc])
                ev = _evaluations_for([fc.id], version).get(fc.id)
                reason = None if ev else "observation_missing"
            except observations.ObservationsUnavailable:
                reason = "observation_unavailable"
    return serialize_evaluation(fc, ev, reason=reason)


def evaluate_day(day: date) -> dict:
    """Precisión agregada del día (tarjeta «Precisión del pronóstico»). Evalúa lo pendiente ya vencido."""
    loc = location()
    chosen = _day_references(day)
    params = active_params()
    evals = _evaluations_for([fc.id for fc in chosen], params.version)
    cutoff = _evaluable_before()
    todo = [fc for fc in chosen if fc.id not in evals and as_utc(fc.target_at) <= cutoff]
    if todo:
        try:
            evaluate_batch(todo)
            evals = _evaluations_for([fc.id for fc in chosen], params.version)
        except observations.ObservationsUnavailable:
            log.info("Evaluación diferida: sin observaciones disponibles para %s", day)
    return {"location": loc.name, "date": day.isoformat(), **_day_evaluation_summary(chosen, evals, params)}


def evaluate_pending() -> int:
    """Tarea horaria: evalúa los pronósticos de referencia vencidos de las últimas horas."""
    cfg = current_app.config
    end = utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=cfg["EVAL_GRACE_HOURS"])
    start = end - timedelta(hours=cfg["EVAL_LOOKBACK_HOURS"])
    refs = reference_forecasts(start, end + timedelta(hours=1))
    params = active_params()
    done = set(_evaluations_for([fc.id for fc in refs.values()], params.version))
    todo = [fc for fc in refs.values() if fc.id not in done]
    if not todo:
        return 0
    try:
        count = evaluate_batch(todo)
    except observations.ObservationsUnavailable:
        return 0
    log.info("Evaluados %s pronósticos", count)
    return count
