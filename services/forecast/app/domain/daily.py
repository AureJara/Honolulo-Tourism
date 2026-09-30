"""Resumen de un día a partir de sus pronósticos horarios (lógica pura, sin BD ni HTTP).

Todo se *deriva* de datos reales del proveedor (RN03): franjas horarias, condición del día,
texto resumen y niveles. No se inventa ningún valor meteorológico.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from honolulo_common.conditions import CONDITIONS, describe

# Horario operativo del santuario (el diseño muestra franjas de 08:00 a 18:00).
OPERATING_START = 8
OPERATING_END = 18
BANDS = (("08-12", 8, 12), ("12-16", 12, 16), ("16-18", 16, 18))

_RAINY = {"drizzle", "light_rain", "rain", "heavy_rain", "thunderstorm"}


@dataclass
class HourPoint:
    hour: int                       # hora local (0-23)
    temperature: float
    condition: str                  # código de nuestro enumerado
    humidity: int | None = None
    rain_probability: int | None = None
    precipitation: float | None = None
    wind_speed: float | None = None
    cloud_cover: int | None = None
    feels_like: float | None = None
    forecast_id: int | None = None
    lead_hours: float | None = None


def most_common_condition(codes: list[str]) -> str:
    """Condición más frecuente; en empate gana la más severa."""
    counts = Counter(codes)
    return max(counts, key=lambda c: (counts[c], CONDITIONS[c].severity))


def band_label(condition: str, temperature: float, wind_speed: float | None) -> str:
    if condition == "fog":
        return "Bruma fresca"
    if condition == "clear":
        return "Sol radiante" if temperature >= 25 else "Despejado"
    if condition == "partly_cloudy":
        return "Sol con nubes"
    if condition == "cloudy":
        return "Brisa suave" if (wind_speed or 0) >= 8 else "Nublado"
    return CONDITIONS[condition].text


def build_bands(points: list[HourPoint]) -> list[dict]:
    """Franjas 08-12, 12-16 y 16-18: temperatura media y condición dominante."""
    bands = []
    for key, start, end in BANDS:
        chunk = [p for p in points if start <= p.hour < end]
        if not chunk:
            continue
        temperature = round(sum(p.temperature for p in chunk) / len(chunk))
        condition = most_common_condition([p.condition for p in chunk])
        winds = [p.wind_speed for p in chunk if p.wind_speed is not None]
        wind = sum(winds) / len(winds) if winds else None
        cond = describe(condition)
        bands.append({
            "key": key, "from": f"{start:02d}:00", "to": f"{end:02d}:00",
            "temperature": temperature, "condition_code": condition, "condition": cond.text,
            "icon": cond.icon, "label": band_label(condition, temperature, wind),
        })
    return bands


def day_condition(points: list[HourPoint], bands: list[dict]) -> str:
    """Condición representativa: si llueve en algún momento manda la peor; si no, la del mediodía."""
    rainy = [p.condition for p in points if p.condition in _RAINY]
    if rainy:
        return max(rainy, key=lambda c: CONDITIONS[c].severity)
    midday = next((b for b in bands if b["key"] == "12-16"), None)
    if midday:
        return midday["condition_code"]
    return most_common_condition([p.condition for p in points])


_MAIN_PHRASE = {
    "clear": "Soleado", "partly_cloudy": "Parcialmente nublado", "cloudy": "Nublado",
    "fog": "Con neblina", "drizzle": "Con llovizna", "light_rain": "Con lluvia ligera",
    "rain": "Lluvioso", "heavy_rain": "Con lluvia intensa", "thunderstorm": "Con tormentas",
}


def summary_text(condition: str, bands: list[dict], temperature: float) -> str:
    """Ej.: «Soleado con niebla matinal templada» (texto del diseño)."""
    main = _MAIN_PHRASE[condition]
    foggy_morning = bool(bands) and bands[0]["condition_code"] == "fog" and condition != "fog"
    cool, warm = temperature < 22, temperature > 27
    if foggy_morning:
        adjective = "fresca" if cool else "cálida" if warm else "templada"
        return f"{main} con niebla matinal {adjective}"
    adjective = "fresco" if cool else "cálido" if warm else "templado"
    return f"{main} y {adjective}"


def rain_level(probability: int | None) -> tuple[str, str] | None:
    if probability is None:
        return None
    if probability < 30:
        return "low", "Baja"
    if probability < 60:
        return "medium", "Media"
    return "high", "Alta"


def humidity_level(humidity: int | None) -> tuple[str, str] | None:
    if humidity is None:
        return None
    if humidity < 50:
        return "dry", "Seca"
    if humidity <= 85:
        return "optimal", "Óptima"
    return "very_humid", "Muy húmeda"


def _avg(values: list, digits: int = 0):
    values = [v for v in values if v is not None]
    if not values:
        return None
    mean = sum(values) / len(values)
    return round(mean) if digits == 0 else round(mean, digits)


def build_metrics(points: list[HourPoint]) -> dict:
    """Métricas del día (RF05): lluvia, humedad, viento y precipitación."""
    probs = [p.rain_probability for p in points if p.rain_probability is not None]
    rain = max(probs) if probs else None
    humidity = _avg([p.humidity for p in points])
    rain_lvl, hum_lvl = rain_level(rain), humidity_level(humidity)
    precip = [p.precipitation for p in points if p.precipitation is not None]
    winds = [p.wind_speed for p in points if p.wind_speed is not None]
    return {
        "rain_probability": {"value": rain, "level": rain_lvl[0] if rain_lvl else None,
                             "label": rain_lvl[1] if rain_lvl else None},
        "humidity": {"value": humidity, "level": hum_lvl[0] if hum_lvl else None,
                     "label": hum_lvl[1] if hum_lvl else None},
        "wind_speed": {"value": round(sum(winds) / len(winds), 1) if winds else None,
                       "max": round(max(winds), 1) if winds else None},
        "precipitation": {"value": round(sum(precip), 1) if precip else None},
    }


def summarize_day(points: list[HourPoint]) -> dict | None:
    """Resumen del día (temperatura = máxima del horario operativo, como en el diseño)."""
    points = sorted((p for p in points if OPERATING_START <= p.hour < OPERATING_END), key=lambda p: p.hour)
    if not points:
        return None
    bands = build_bands(points)
    condition = day_condition(points, bands)
    temperature = max(p.temperature for p in points)
    cond = describe(condition)
    return {
        "temperature": temperature, "condition": cond.text, "condition_code": condition,
        "icon": cond.icon, "summary": summary_text(condition, bands, temperature),
        "bands": bands, "metrics": build_metrics(points),
    }
