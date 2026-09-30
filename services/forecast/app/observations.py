"""Cliente de weather-service: obtiene la serie horaria observada (RN06)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests
from flask import current_app

from honolulo_common.timeutil import parse_iso_datetime

log = logging.getLogger(__name__)


class ObservationsUnavailable(Exception):
    """weather-service no respondió o devolvió datos inválidos."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_hourly_observations(start: datetime, end: datetime) -> dict[datetime, dict]:
    """Devuelve ``{hora_utc: observación}`` del rango (pide a weather-service completar huecos)."""
    cfg = current_app.config
    try:
        resp = requests.get(
            f"{cfg['WEATHER_SERVICE_URL'].rstrip('/')}/internal/observations",
            params={"from": _iso(start), "to": _iso(end), "ensure": "1"},
            headers={"X-Internal-Token": cfg["INTERNAL_API_TOKEN"]},
            timeout=cfg["WEATHER_SERVICE_TIMEOUT_S"])
        resp.raise_for_status()
        items = resp.json()["items"]
        return {parse_iso_datetime(i["observed_at"], "observed_at"): i for i in items}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        log.warning("No se pudieron obtener observaciones de weather-service: %s", exc)
        raise ObservationsUnavailable(str(exc)) from exc
