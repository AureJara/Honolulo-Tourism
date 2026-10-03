"""Interfaz de los proveedores meteorológicos y registro para elegirlos por configuración.

Los servicios ``weather`` y ``forecast`` dependen solo de esta abstracción (``WeatherProvider``), nunca de un
proveedor concreto. Principio abierto/cerrado: para usar otro proveedor se escribe un módulo nuevo con una clase que
cumpla la interfaz y una función registrada con ``@register_provider("nombre")``; después basta
``WEATHER_PROVIDER=nombre`` (y, si el módulo no es uno de los incluidos, ``WEATHER_PROVIDER_MODULES=mi.modulo``).
Ni los servicios ni este archivo se modifican.

Los datos nunca se inventan (RN03): si el proveedor falla se lanza ``ProviderError``.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

DEFAULT_PROVIDER = "open-meteo"
BUILTIN_MODULES = ("honolulo_common.openmeteo",)          # módulos que registran los proveedores incluidos


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


@runtime_checkable
class WeatherProvider(Protocol):
    """Lo mínimo que un proveedor debe ofrecer."""

    name: str                                   # identificador que se guarda junto a cada dato (p. ej. «open-meteo»)
    last_stats: FetchStats | None               # estadísticas de la última consulta (solo informativas)

    def fetch_current(self, lat: float, lon: float, tz: str) -> Reading: ...

    def fetch_hourly(self, lat: float, lon: float, tz: str, *, forecast_days: int = 16,
                     past_days: int = 0) -> list[Reading]: ...


ProviderFactory = Callable[[Mapping[str, Any]], WeatherProvider]
_PROVIDERS: dict[str, ProviderFactory] = {}


def register_provider(name: str) -> Callable[[ProviderFactory], ProviderFactory]:
    """Registra la fábrica de un proveedor: recibe la configuración del servicio y devuelve el cliente."""
    def decorator(factory: ProviderFactory) -> ProviderFactory:
        _PROVIDERS[name] = factory
        return factory
    return decorator


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def create_provider(config: Mapping[str, Any]) -> WeatherProvider:
    """Construye el proveedor elegido en ``WEATHER_PROVIDER`` (por defecto Open-Meteo)."""
    extra = [m.strip() for m in str(config.get("WEATHER_PROVIDER_MODULES") or "").split(",") if m.strip()]
    for module in (*BUILTIN_MODULES, *extra):
        importlib.import_module(module)                   # al importarse, cada módulo registra sus proveedores
    name = config.get("WEATHER_PROVIDER") or DEFAULT_PROVIDER
    try:
        factory = _PROVIDERS[name]
    except KeyError:
        raise RuntimeError(f"WEATHER_PROVIDER desconocido: {name!r}. Disponibles: {', '.join(available_providers())}") from None
    return factory(config)
