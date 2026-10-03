"""Lógica de negocio del clima actual, el historial y la serie horaria observada."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy.dialects.postgresql import insert as pg_insert

from honolulo_common.conditions import condition_code_from_wmo, describe
from honolulo_common.errors import ApiError
from honolulo_common.location import Location
from honolulo_common.weather_provider import ProviderError, Reading
from honolulo_common.timeutil import as_utc, iso_local, local_date, local_hour_to_utc, utcnow

from .extensions import db
from .models import ProviderFetchLog, WeatherObservation

log = logging.getLogger(__name__)

# Mensajes exigidos por el spec (RN08 / Escenario 5).
RN08_MESSAGE = "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."
UNAVAILABLE_MESSAGE = "El servicio meteorológico no está disponible. Inténtelo nuevamente."

_UPSERT_CONSTRAINT = "uq_obs_location_time_source"
_KEY_COLUMNS = ("location_code", "observed_at", "source")


def location() -> Location:
    return current_app.extensions["location"]


def provider():
    return current_app.extensions["weather_client"]


# ----------------------------------------------------------------- bitácora
def _log_fetch(kind: str, ok: bool, error: str | None = None) -> None:
    stats = getattr(provider(), "last_stats", None)
    db.session.add(ProviderFetchLog(
        provider=provider().name, kind=kind, ok=ok,
        http_status=getattr(stats, "http_status", None), latency_ms=getattr(stats, "latency_ms", None),
        error=(error or "")[:300] or None, fetched_at=utcnow()))
    db.session.commit()


def _last_attempt_at(kind: str) -> datetime | None:
    row = db.session.execute(
        db.select(ProviderFetchLog.fetched_at).where(ProviderFetchLog.kind == kind)
        .order_by(ProviderFetchLog.fetched_at.desc()).limit(1)).first()
    return as_utc(row[0]) if row else None


# ------------------------------------------------------------ persistencia
def _row_from_reading(reading: Reading, *, source: str, fetched_at: datetime) -> dict:
    return {
        "location_code": location().code, "observed_at": reading.time_utc,
        "temperature_c": reading.temperature_c, "feels_like_c": reading.feels_like_c,
        "humidity_pct": reading.humidity_pct, "precipitation_mm": reading.precipitation_mm or 0,
        "rain_probability_pct": reading.rain_probability_pct, "wind_kph": reading.wind_kph,
        "cloud_cover_pct": reading.cloud_cover_pct, "weather_code": reading.weather_code,
        "condition": condition_code_from_wmo(reading.weather_code), "is_day": reading.is_day,
        "source": source, "provider": provider().name, "fetched_at": fetched_at,
    }


def _upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(WeatherObservation).values(rows)
    updatable = {c: stmt.excluded[c] for c in rows[0] if c not in _KEY_COLUMNS}
    stmt = stmt.on_conflict_do_update(constraint=_UPSERT_CONSTRAINT, set_=updatable)
    db.session.execute(stmt)
    db.session.commit()
    return len(rows)  # cursor.rowcount no es fiable en INSERT multi-fila con psycopg


def latest_current() -> WeatherObservation | None:
    return db.session.execute(
        db.select(WeatherObservation)
        .where(WeatherObservation.location_code == location().code, WeatherObservation.source == "current")
        .order_by(WeatherObservation.fetched_at.desc()).limit(1)).scalar_one_or_none()


# ------------------------------------------------------------ clima actual
def refresh_current() -> WeatherObservation:
    """Consulta al proveedor y guarda la lectura (RF08). Lanza ProviderError si falla (RN03)."""
    loc = location()
    try:
        reading = provider().fetch_current(loc.latitude, loc.longitude, loc.timezone)
    except ProviderError as exc:
        log.warning("Fallo al consultar clima actual: %s", exc)
        _log_fetch("current", False, str(exc))
        raise
    _log_fetch("current", True)
    _upsert([_row_from_reading(reading, source="current", fetched_at=utcnow())])
    return db.session.execute(
        db.select(WeatherObservation).where(
            WeatherObservation.location_code == loc.code,
            WeatherObservation.observed_at == reading.time_utc,
            WeatherObservation.source == "current")).scalar_one()


def serialize_weather(obs: WeatherObservation) -> dict:
    cond = describe(obs.condition, obs.is_day is not False)
    return {
        "temperature": obs.temperature_c, "feels_like": obs.feels_like_c,
        "humidity": obs.humidity_pct, "precipitation": obs.precipitation_mm,
        "rain_probability": obs.rain_probability_pct, "wind_speed": obs.wind_kph,
        "cloud_cover": obs.cloud_cover_pct, "condition": cond.text,
        "condition_code": obs.condition, "icon": cond.icon,
    }


def serialize_current(obs: WeatherObservation, *, stale: bool, message: str | None = None,
                      now: datetime | None = None) -> dict:
    """Contrato de ``GET /api/v1/weather/current`` (RN04: incluye cuándo se obtuvo el dato)."""
    loc = location()
    now = now or utcnow()
    body = {
        "location": loc.name,
        "updated_at": iso_local(obs.fetched_at, loc.timezone),
        "observed_at": iso_local(obs.observed_at, loc.timezone),
        "weather": serialize_weather(obs),
        "stale": stale,
        "age_seconds": max(0, int((now - as_utc(obs.fetched_at)).total_seconds())),
    }
    if message:
        body["message"] = message
    return body


def get_current() -> dict:
    """Devuelve el clima actual; si el dato venció intenta refrescarlo (RN08, Escenario 5)."""
    cfg = current_app.config
    now = utcnow()
    latest = latest_current()
    if latest and (now - as_utc(latest.fetched_at)) <= timedelta(minutes=cfg["CURRENT_MAX_AGE_MIN"]):
        return serialize_current(latest, stale=False, now=now)
    try:
        return serialize_current(refresh_current(), stale=False)
    except ProviderError:
        if latest:
            # Se muestra el último dato guardado, marcado claramente como anterior.
            return serialize_current(latest, stale=True, message=RN08_MESSAGE, now=now)
        raise ApiError(503, "WEATHER_UNAVAILABLE", UNAVAILABLE_MESSAGE) from None


def manual_refresh() -> dict:
    """RF07 «Actualizar clima», con intervalo mínimo global entre consultas al proveedor."""
    cfg = current_app.config
    tz = location().timezone
    now = utcnow()
    interval = timedelta(seconds=cfg["REFRESH_MIN_INTERVAL_S"])
    latest = latest_current()
    last_attempt = _last_attempt_at("current")

    def last_known():
        return {"last_known": serialize_current(latest, stale=True, message=RN08_MESSAGE, now=now)} if latest else {}

    if last_attempt and now - last_attempt < interval:
        next_allowed = iso_local(last_attempt + interval, tz)
        if latest and as_utc(latest.fetched_at) >= last_attempt - timedelta(seconds=1):
            body = serialize_current(latest, stale=False, now=now)
            body.update(refreshed=False, reason="recent", next_allowed_at=next_allowed)
            return body
        # El último intento falló hace poco: no se insiste contra el proveedor.
        raise ApiError(502, "WEATHER_PROVIDER_UNAVAILABLE", RN08_MESSAGE, extra=last_known())
    try:
        obs = refresh_current()
    except ProviderError:
        raise ApiError(502, "WEATHER_PROVIDER_UNAVAILABLE", RN08_MESSAGE, extra=last_known()) from None
    body = serialize_current(obs, stale=False)
    body.update(refreshed=True, next_allowed_at=iso_local(utcnow() + interval, tz))
    return body


# --------------------------------------------------------------- historial
def serialize_history_item(obs: WeatherObservation) -> dict:
    loc = location()
    local = as_utc(obs.observed_at).astimezone(ZoneInfo(loc.timezone))
    cond = describe(obs.condition, obs.is_day is not False)
    return {
        "id": obs.id, "location": loc.name,
        "date": local.date().isoformat(), "time": local.strftime("%H:%M"),
        "observed_at": iso_local(obs.observed_at, loc.timezone),
        "temperature": obs.temperature_c, "feels_like": obs.feels_like_c,
        "humidity": obs.humidity_pct, "precipitation": obs.precipitation_mm,
        "rain_probability": obs.rain_probability_pct, "wind_speed": obs.wind_kph,
        "cloud_cover": obs.cloud_cover_pct, "condition": cond.text,
        "condition_code": obs.condition, "source": obs.source,
    }


def history(*, start: date | None, end: date | None, source: str, page: int, page_size: int) -> dict:
    loc = location()
    conditions = [WeatherObservation.location_code == loc.code]
    if start:
        conditions.append(WeatherObservation.observed_at >= local_hour_to_utc(start, 0, loc.timezone))
    if end:
        conditions.append(WeatherObservation.observed_at < local_hour_to_utc(end + timedelta(days=1), 0, loc.timezone))
    if source != "all":
        conditions.append(WeatherObservation.source == source)
    total = db.session.execute(db.select(db.func.count()).select_from(WeatherObservation).where(*conditions)).scalar_one()
    rows = db.session.execute(
        db.select(WeatherObservation).where(*conditions)
        .order_by(WeatherObservation.observed_at.desc(), WeatherObservation.id.desc())
        .offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return {"location": loc.name, "items": [serialize_history_item(r) for r in rows],
            "page": page, "page_size": page_size, "total": total}


# ------------------------------------------- serie horaria (insumo de evaluación)
def sync_hourly(past_days: int | None = None) -> int:
    """Guarda la serie horaria del proveedor para las horas ya cumplidas."""
    loc = location()
    days = past_days or current_app.config["HOURLY_SYNC_DAYS"]
    try:
        points = provider().fetch_hourly(loc.latitude, loc.longitude, loc.timezone,
                                         forecast_days=1, past_days=days)
    except ProviderError as exc:
        log.warning("Fallo al sincronizar la serie horaria: %s", exc)
        _log_fetch("hourly", False, str(exc))
        raise
    _log_fetch("hourly", True)
    now = utcnow()
    rows = [_row_from_reading(p, source="hourly", fetched_at=now)
            for p in points if p.time_utc <= now and p.temperature_c is not None]
    return _upsert(rows)


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def hourly_observations(start: datetime, end: datetime) -> list[WeatherObservation]:
    return list(db.session.execute(
        db.select(WeatherObservation).where(
            WeatherObservation.location_code == location().code,
            WeatherObservation.source == "hourly",
            WeatherObservation.observed_at >= start, WeatherObservation.observed_at <= end)
        .order_by(WeatherObservation.observed_at)).scalars())


def ensure_hourly(start: datetime, end: datetime) -> list[WeatherObservation]:
    """Devuelve las observaciones horarias del rango; si faltan horas cumplidas intenta traerlas."""
    start, end = as_utc(start), as_utc(end)
    now = utcnow()
    rows = hourly_observations(start, end)
    have = {as_utc(r.observed_at) for r in rows}

    first = _floor_hour(start) if _floor_hour(start) >= start else _floor_hour(start) + timedelta(hours=1)
    limit = min(end, now - timedelta(hours=1))
    missing, hour = [], first
    while hour <= limit:
        if hour not in have:
            missing.append(hour)
        hour += timedelta(hours=1)

    if missing:
        last = _last_attempt_at("hourly")
        interval = timedelta(seconds=current_app.config["HOURLY_SYNC_MIN_INTERVAL_S"])
        if last is None or now - last >= interval:
            # past_days cuenta días calendario locales completos hacia atrás (más el día en curso).
            tz = location().timezone
            days = min(92, max(1, (local_date(now, tz) - local_date(min(missing), tz)).days))
            try:
                sync_hourly(days)
                rows = hourly_observations(start, end)
            except ProviderError:
                pass  # se devuelve lo que exista; el evaluador reintentará en su próxima corrida
    return rows


def serialize_internal(obs: WeatherObservation) -> dict:
    return {
        "id": obs.id, "observed_at": as_utc(obs.observed_at).isoformat().replace("+00:00", "Z"),
        "temperature": obs.temperature_c, "feels_like": obs.feels_like_c, "humidity": obs.humidity_pct,
        "precipitation": obs.precipitation_mm, "rain_probability": obs.rain_probability_pct,
        "wind_speed": obs.wind_kph, "cloud_cover": obs.cloud_cover_pct,
        "condition_code": obs.condition, "weather_code": obs.weather_code,
    }
