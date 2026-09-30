"""Cliente del proveedor meteorológico externo (Open-Meteo, sin API key).

Es el único punto de contacto con el proveedor: si se cambia de proveedor solo hay que
reemplazar este módulo manteniendo la interfaz (``fetch_current`` / ``fetch_hourly``).
Los datos nunca se inventan (RN03): si el proveedor falla se lanza ``ProviderError``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.open-meteo.com/v1/forecast"
PROVIDER_NAME = "open-meteo"

_HOURLY_VARS = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation_probability,"
    "precipitation,weather_code,cloud_cover,wind_speed_10m,is_day"
)
_CURRENT_VARS = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
    "weather_code,cloud_cover,wind_speed_10m,is_day"
)


class ProviderError(Exception):
    """El proveedor no respondió o devolvió datos inválidos."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


@dataclass
class Reading:
    """Una lectura meteorológica normalizada (instantes en UTC)."""

    time_utc: datetime
    temperature_c: float | None
    feels_like_c: float | None
    humidity_pct: int | None
    precipitation_mm: float | None
    rain_probability_pct: int | None
    wind_kph: float | None
    cloud_cover_pct: int | None
    weather_code: int | None
    is_day: bool | None


@dataclass
class FetchStats:
    latency_ms: int
    http_status: int | None


def _num(value, cast=float):
    return None if value is None else cast(value)


def _local_to_utc(text: str, tz: ZoneInfo) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=tz).astimezone(timezone.utc)


class OpenMeteoClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 10.0,
                 retries: int = 1, session: requests.Session | None = None) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.session = session or requests.Session()
        self.last_stats: FetchStats | None = None

    # ------------------------------------------------------------------ HTTP
    def _get(self, params: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            started = time.monotonic()
            status = None
            try:
                resp = self.session.get(self.base_url, params=params, timeout=self.timeout)
                status = resp.status_code
                self.last_stats = FetchStats(int((time.monotonic() - started) * 1000), status)
                if status >= 500 or status == 429:
                    raise ProviderError(f"El proveedor respondió HTTP {status}", http_status=status)
                if status >= 400:
                    # Error del cliente (parámetros): reintentar no ayuda.
                    raise ProviderError(f"Solicitud rechazada por el proveedor (HTTP {status})",
                                        http_status=status)
                data = resp.json()
                if not isinstance(data, dict):
                    raise ProviderError("Respuesta inesperada del proveedor")
                return data
            except ProviderError as exc:
                last_error = exc
                if exc.http_status and exc.http_status < 500 and exc.http_status != 429:
                    break
            except (requests.RequestException, ValueError) as exc:
                self.last_stats = FetchStats(int((time.monotonic() - started) * 1000), status)
                last_error = ProviderError(f"No se pudo contactar al proveedor: {exc}")
            if attempt < self.retries:
                time.sleep(0.4 * (attempt + 1))
        assert last_error is not None
        raise last_error if isinstance(last_error, ProviderError) else ProviderError(str(last_error))

    def _base_params(self, lat: float, lon: float, tz: str) -> dict:
        return {
            "latitude": lat, "longitude": lon, "timezone": tz,
            "temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm",
        }

    # -------------------------------------------------------------- Parsing
    @staticmethod
    def _parse_hourly(data: dict, tz: ZoneInfo) -> list[Reading]:
        hourly = data.get("hourly")
        if not isinstance(hourly, dict) or "time" not in hourly:
            raise ProviderError("Respuesta del proveedor sin datos horarios")
        times = hourly["time"]

        def col(name):
            values = hourly.get(name)
            return values if isinstance(values, list) and len(values) == len(times) else [None] * len(times)

        temp, feels, hum = col("temperature_2m"), col("apparent_temperature"), col("relative_humidity_2m")
        prob, prec, code = col("precipitation_probability"), col("precipitation"), col("weather_code")
        cloud, wind, day = col("cloud_cover"), col("wind_speed_10m"), col("is_day")
        readings = []
        for i, t in enumerate(times):
            if temp[i] is None:  # hora sin dato útil
                continue
            readings.append(Reading(
                time_utc=_local_to_utc(t, tz),
                temperature_c=_num(temp[i]), feels_like_c=_num(feels[i]),
                humidity_pct=_num(hum[i], lambda v: int(round(v))),
                precipitation_mm=_num(prec[i]),
                rain_probability_pct=_num(prob[i], lambda v: int(round(v))),
                wind_kph=_num(wind[i]),
                cloud_cover_pct=_num(cloud[i], lambda v: int(round(v))),
                weather_code=_num(code[i], int),
                is_day=None if day[i] is None else bool(day[i]),
            ))
        return readings

    # ------------------------------------------------------------ Interfaz
    def fetch_hourly(self, lat: float, lon: float, tz: str, *, forecast_days: int = 16,
                     past_days: int = 0) -> list[Reading]:
        params = self._base_params(lat, lon, tz)
        params.update(hourly=_HOURLY_VARS, forecast_days=forecast_days)
        if past_days:
            params["past_days"] = past_days
        return self._parse_hourly(self._get(params), ZoneInfo(tz))

    def fetch_current(self, lat: float, lon: float, tz: str) -> Reading:
        """Condiciones actuales. La probabilidad de lluvia se toma de la hora en curso."""
        params = self._base_params(lat, lon, tz)
        params.update(current=_CURRENT_VARS, hourly="precipitation_probability", forecast_days=1)
        data = self._get(params)
        cur = data.get("current")
        if not isinstance(cur, dict) or cur.get("time") is None or cur.get("temperature_2m") is None:
            raise ProviderError("Respuesta del proveedor sin condiciones actuales")
        zone = ZoneInfo(tz)
        observed = _local_to_utc(cur["time"], zone)

        rain_prob = None
        hourly = data.get("hourly") or {}
        hours, probs = hourly.get("time") or [], hourly.get("precipitation_probability") or []
        hour_key = cur["time"][:13] + ":00"
        if hour_key in hours and len(probs) == len(hours):
            rain_prob = _num(probs[hours.index(hour_key)], lambda v: int(round(v)))

        return Reading(
            time_utc=observed,
            temperature_c=_num(cur.get("temperature_2m")), feels_like_c=_num(cur.get("apparent_temperature")),
            humidity_pct=_num(cur.get("relative_humidity_2m"), lambda v: int(round(v))),
            precipitation_mm=_num(cur.get("precipitation")),
            rain_probability_pct=rain_prob,
            wind_kph=_num(cur.get("wind_speed_10m")),
            cloud_cover_pct=_num(cur.get("cloud_cover"), lambda v: int(round(v))),
            weather_code=_num(cur.get("weather_code"), int),
            is_day=None if cur.get("is_day") is None else bool(cur["is_day"]),
        )
