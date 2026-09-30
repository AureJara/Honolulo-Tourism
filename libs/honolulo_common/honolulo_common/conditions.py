"""Catálogo de condiciones climáticas y traducción de códigos WMO (Open-Meteo)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Condition:
    code: str
    text: str       # texto en español mostrado al usuario
    icon: str       # nombre de Material Symbols
    severity: int   # orden para comparar condiciones (evaluación del pronóstico)


_CONDITIONS = [
    Condition("clear", "Soleado", "wb_sunny", 0),
    Condition("partly_cloudy", "Parcialmente nublado", "partly_cloudy_day", 1),
    Condition("cloudy", "Nublado", "cloud", 2),
    Condition("fog", "Neblina", "foggy", 2),
    Condition("drizzle", "Llovizna", "water_drop", 3),
    Condition("light_rain", "Lluvia ligera", "rainy", 4),
    Condition("rain", "Lluvia", "rainy", 5),
    Condition("heavy_rain", "Lluvia intensa", "rainy", 6),
    Condition("thunderstorm", "Tormenta", "thunderstorm", 7),
]
CONDITIONS: dict[str, Condition] = {c.code: c for c in _CONDITIONS}

# Variantes nocturnas (solo cambia la presentación de cielo despejado / parcialmente nublado).
_NIGHT = {
    "clear": Condition("clear", "Despejado", "nights_stay", 0),
    "partly_cloudy": Condition("partly_cloudy", "Parcialmente nublado", "partly_cloudy_night", 1),
}

WMO_TO_CODE: dict[int, str] = {
    0: "clear", 1: "clear", 2: "partly_cloudy", 3: "cloudy",
    45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle",
    61: "light_rain", 66: "light_rain", 80: "light_rain",
    63: "rain", 67: "rain", 81: "rain",
    65: "heavy_rain", 82: "heavy_rain",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}


def condition_code_from_wmo(wmo_code: int | None) -> str:
    """Traduce un código WMO a nuestro enumerado. Los desconocidos caen en ``cloudy``."""
    if wmo_code is None:
        return "cloudy"
    code = WMO_TO_CODE.get(int(wmo_code))
    if code is None:
        log.warning("Código WMO no mapeado: %s", wmo_code)
        return "cloudy"
    return code


def describe(code: str, is_day: bool = True) -> Condition:
    """Devuelve texto/icono de una condición; usa la variante nocturna si aplica."""
    if not is_day and code in _NIGHT:
        return _NIGHT[code]
    return CONDITIONS.get(code, CONDITIONS["cloudy"])
