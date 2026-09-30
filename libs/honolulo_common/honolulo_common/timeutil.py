"""Utilidades de fecha/hora. Instantes en UTC; fechas de negocio en la zona del lugar (Lima)."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .errors import ApiError

UTC = timezone.utc
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    """Normaliza a UTC (asume UTC si el datetime viene sin zona, p. ej. desde SQLite)."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def iso_local(dt: datetime | None, tz: str) -> str | None:
    """ISO 8601 con desfase en la zona del lugar, p. ej. ``2026-09-30T14:00:00-05:00``."""
    if dt is None:
        return None
    return as_utc(dt).astimezone(ZoneInfo(tz)).replace(microsecond=0).isoformat()


def local_hour_to_utc(day: date, hour: int, tz: str) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=ZoneInfo(tz)).astimezone(UTC)


def local_date(dt: datetime, tz: str) -> date:
    return as_utc(dt).astimezone(ZoneInfo(tz)).date()


def parse_date(value: str | None, field: str = "date") -> date:
    if not value:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": field, "message": "Es obligatorio (formato AAAA-MM-DD)."}])
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": field, "message": "Formato inválido, use AAAA-MM-DD."}]) from exc


def parse_month(value: str | None, field: str = "month") -> tuple[date, date]:
    """Devuelve (primer día, último día) del mes ``AAAA-MM``."""
    if not value or not _MONTH_RE.match(value):
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": field, "message": "Formato inválido, use AAAA-MM."}])
    year, month = int(value[:4]), int(value[5:7])
    first = date(year, month, 1)
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    return first, nxt - timedelta(days=1)


def parse_iso_datetime(value: str | None, field: str) -> datetime:
    if not value:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": field, "message": "Es obligatorio (ISO 8601)."}])
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": field, "message": "Formato ISO 8601 inválido."}]) from exc
    return as_utc(parsed)
