"""Modelos de predicción de la afluencia de visitantes. Solo biblioteca estándar (sin numpy ni statsmodels).

Hay dos modelos y un respaldo:

* **Diario**: perfil semanal × estacionalidad mensual × feriados, con el nivel reciente (últimas 4 semanas). Opcionalmente
  se ajusta por la probabilidad de lluvia del pronóstico (un *supuesto*, no un valor aprendido: no hay histórico de
  clima emparejado con las visitas).
* **Mensual**: Holt-Winters multiplicativo (nivel + tendencia amortiguada + estacionalidad anual). Necesita al menos dos
  años de datos.
* **Respaldo mensual** (menos de dos años de datos propios): nivel reciente × índice estacional regional de Huánuco
  (arribos a hospedajes, BCRP/MINCETUR 2015-2019, ``data/``).

Todas las funciones son puras: reciben series y devuelven números, para poder probarlas sin base de datos.
"""

from __future__ import annotations

import functools
import json
import math
import statistics
from datetime import date, timedelta
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "bcrp_huanuco_arribos_2015_2019.json"
SEASON = 12
Z95 = 1.96
RAIN_EFFECT = 0.30            # supuesto: con 100 % de probabilidad de lluvia se espera 30 % menos de visitas
DEFAULT_HOLIDAY_FACTOR = 1.3
DOW_DEFAULT = (0.75, 0.70, 0.72, 0.80, 1.10, 1.55, 1.45)      # lunes … domingo (solo si hay poco histórico)


# ----------------------------------------------------------------- referencia regional (BCRP)
@functools.lru_cache(maxsize=1)
def regional() -> dict:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def regional_seasonal_index() -> tuple[float, ...]:
    """Índice estacional de cada mes (ene…dic) con media 1, según los arribos mensuales de Huánuco."""
    monthly = regional()["monthly"]
    years = sorted({int(k[:4]) for k in monthly})
    per_month: list[list[float]] = [[] for _ in range(12)]
    for year in years:
        values = [monthly[f"{year}-{m:02d}"] for m in range(1, 13)]
        mean = statistics.fmean(values)
        for m, v in enumerate(values):
            per_month[m].append(v / mean)
    index = [statistics.fmean(vals) for vals in per_month]
    mean = statistics.fmean(index)
    return tuple(round(v / mean, 4) for v in index)


# ----------------------------------------------------------------- feriados del Perú
def easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = ((h + el - 7 * m + 114) % 31) + 1
    return date(year, month, day)


@functools.lru_cache(maxsize=64)
def holidays(year: int) -> frozenset[date]:
    fixed = [(1, 1), (5, 1), (6, 29), (7, 28), (7, 29), (8, 30), (10, 8), (11, 1), (12, 8), (12, 25)]
    pascua = easter(year)
    return frozenset({date(year, m, d) for m, d in fixed} | {pascua - timedelta(days=3), pascua - timedelta(days=2)})


def is_holiday(day: date) -> bool:
    return day in holidays(day.year)


# ----------------------------------------------------------------- utilidades
def mape(actual: list[float], predicted: list[float]) -> float | None:
    """Error porcentual absoluto medio (ignora los días con valor real 0)."""
    pairs = [(a, p) for a, p in zip(actual, predicted) if a and a > 0]
    if not pairs:
        return None
    return round(100 * statistics.fmean(abs(a - p) / a for a, p in pairs), 1)


def month_add(year: int, month: int, n: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + n
    return total // 12, total % 12 + 1


def fill_gaps(values: list[float | None]) -> list[float]:
    """Interpola linealmente los meses sin dato (los extremos se repiten)."""
    out = list(values)
    known = [i for i, v in enumerate(out) if v is not None]
    if not known:
        return [0.0] * len(out)
    for i, v in enumerate(out):
        if v is not None:
            continue
        before = max((k for k in known if k < i), default=None)
        after = min((k for k in known if k > i), default=None)
        if before is None:
            out[i] = out[after]
        elif after is None:
            out[i] = out[before]
        else:
            w = (i - before) / (after - before)
            out[i] = out[before] * (1 - w) + out[after] * w
    return [float(v) for v in out]


# ----------------------------------------------------------------- Holt-Winters multiplicativo
def _hw_pass(y: list[float], alpha: float, beta: float, gamma: float, phi: float):
    n = len(y)
    cycles = n // SEASON
    first = y[:SEASON]
    level = statistics.fmean(first)
    trend = (statistics.fmean(y[SEASON:2 * SEASON]) - level) / SEASON
    seas = []
    for i in range(SEASON):
        ratios = [y[k * SEASON + i] / max(statistics.fmean(y[k * SEASON:(k + 1) * SEASON]), 1e-9) for k in range(cycles)]
        seas.append(max(statistics.fmean(ratios), 0.05))
    errors: list[float] = []
    for t in range(SEASON, n):
        si = t % SEASON
        pred = max((level + phi * trend) * seas[si], 1e-9)
        errors.append((y[t] - pred) / pred)
        new_level = alpha * (y[t] / seas[si]) + (1 - alpha) * (level + phi * trend)
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        seas[si] = max(gamma * (y[t] / max(new_level, 1e-9)) + (1 - gamma) * seas[si], 0.05)
        level = new_level
    return level, trend, seas, errors


def holt_winters(y: list[float], horizon: int, phi: float = 0.95) -> dict | None:
    """Pronostica ``horizon`` meses después de ``y`` (meses consecutivos). Requiere ``len(y) >= 24``."""
    if len(y) < 2 * SEASON:
        return None
    best = None
    for alpha in (0.1, 0.3, 0.5):
        for beta in (0.0, 0.05, 0.15):
            for gamma in (0.1, 0.3, 0.5):
                level, trend, seas, errors = _hw_pass(y, alpha, beta, gamma, phi)
                sse = sum(e * e for e in errors)
                if best is None or sse < best[0]:
                    best = (sse, level, trend, seas, errors, (alpha, beta, gamma))
    _, level, trend, seas, errors, params = best
    sigma = max(statistics.pstdev(errors) if len(errors) > 1 else 0.1, 0.03)
    points = []
    for h in range(1, horizon + 1):
        damp = sum(phi ** i for i in range(1, h + 1))
        mid = max((level + damp * trend) * seas[(len(y) + h - 1) % SEASON], 0.0)
        half = Z95 * sigma * math.sqrt(1 + 0.2 * (h - 1)) * mid
        points.append({"expected": mid, "low": max(mid - half, 0.0), "high": mid + half})
    return {"method": "holt-winters", "sigma": sigma, "params": params, "points": points}


def regional_fallback(y: list[float], first_month: int, horizon: int) -> dict | None:
    """Con menos de dos años de datos: nivel reciente desestacionalizado × índice estacional regional."""
    if len(y) < 3:
        return None
    index = regional_seasonal_index()
    window = y[-min(len(y), 6):]
    start_month = (first_month - 1 + len(y) - len(window)) % 12
    deseason = [v / index[(start_month + i) % 12] for i, v in enumerate(window)]
    level = statistics.fmean(deseason)
    sigma = max(statistics.pstdev(deseason) / level if len(deseason) > 1 and level else 0.1, 0.08)
    points = []
    for h in range(1, horizon + 1):
        mid = max(level * index[(first_month - 1 + len(y) + h - 1) % 12], 0.0)
        half = Z95 * sigma * math.sqrt(1 + 0.2 * (h - 1)) * mid
        points.append({"expected": mid, "low": max(mid - half, 0.0), "high": mid + half})
    return {"method": "regional", "sigma": sigma, "params": None, "points": points}


def forecast_monthly(y: list[float], first_month: int, horizon: int) -> dict | None:
    """``y``: totales de meses completos y consecutivos; ``first_month``: mes (1-12) del primer elemento."""
    return holt_winters(y, horizon) or regional_fallback(y, first_month, horizon)


def backtest_monthly(y: list[float], first_month: int, k: int = 6) -> float | None:
    if len(y) < 3 + k:
        return None
    result = forecast_monthly(y[:-k], first_month, k)
    if result is None:
        return None
    return mape(y[-k:], [p["expected"] for p in result["points"]])


# ----------------------------------------------------------------- modelo diario
def monthly_index_from(history: dict[date, float]) -> tuple[list[float], str]:
    """Índice estacional mensual propio si hay dos años de datos; si no, el regional."""
    per_month: dict[tuple[int, int], list[float]] = {}
    for d, v in history.items():
        per_month.setdefault((d.year, d.month), []).append(v)
    full = [(k, vals) for k, vals in per_month.items() if len(vals) >= 25]
    if len(full) >= 24:
        by_month: list[list[float]] = [[] for _ in range(12)]
        for (_, m), vals in full:
            by_month[m - 1].append(statistics.fmean(vals))
        if all(by_month):
            raw = [statistics.fmean(v) for v in by_month]
            mean = statistics.fmean(raw)
            return [v / mean for v in raw], "propio"
    return list(regional_seasonal_index()), "regional"


def forecast_daily(history: dict[date, float], first_day: date, horizon: int, rain: dict[date, float] | None = None,
                   month_index: list[float] | None = None) -> dict | None:
    """Pronostica ``horizon`` días desde ``first_day`` usando solo los datos anteriores a ``first_day``."""
    days = sorted(d for d in history if d < first_day)
    if len(days) < 28:
        return None
    last = days[-1]
    if month_index is None:
        month_index, _ = monthly_index_from({d: history[d] for d in days})
    recent = [d for d in days if d > last - timedelta(days=84) and not is_holiday(d)]
    overall = statistics.fmean(history[d] for d in recent) if recent else 0.0
    if overall <= 0:
        return None
    dow = []
    for wd in range(7):
        vals = [history[d] / month_index[d.month - 1] for d in recent if d.weekday() == wd]
        dow.append(statistics.fmean(vals) if vals else None)
    base = statistics.fmean(v for v in dow if v is not None)
    dow = [(v / base) if v is not None else DOW_DEFAULT[i] for i, v in enumerate(dow)]

    # efecto de feriado observado: valor del feriado frente al promedio de ese mismo día de la semana cerca de la fecha
    ratios = []
    for d in days:
        if not is_holiday(d) or history[d] <= 0:
            continue
        peers = [history[o] for o in days if not is_holiday(o) and o.weekday() == d.weekday() and abs((o - d).days) <= 21]
        if len(peers) >= 2 and statistics.fmean(peers) > 0:
            ratios.append(history[d] / statistics.fmean(peers))
    holiday = min(max(statistics.fmean(ratios), 1.0), 2.5) if len(ratios) >= 3 else DEFAULT_HOLIDAY_FACTOR

    def shape(day: date) -> float:
        return dow[day.weekday()] * month_index[day.month - 1] * (holiday if is_holiday(day) else 1.0)

    window = [d for d in days if d > last - timedelta(days=28)]
    level = statistics.fmean(history[d] / shape(d) for d in window)
    resid = [history[d] / max(level * shape(d), 1e-9) - 1 for d in days if d > last - timedelta(days=56)]
    sigma = max(statistics.pstdev(resid) if len(resid) > 1 else 0.15, 0.08)

    rain = rain or {}
    points = []
    for h in range(horizon):
        day = first_day + timedelta(days=h)
        p = rain.get(day)
        factor = 1 - RAIN_EFFECT * min(max(p, 0.0), 100.0) / 100 if p is not None else 1.0
        mid = max(level * shape(day) * factor, 0.0)
        half = Z95 * sigma * mid
        points.append({"date": day, "expected": mid, "low": max(mid - half, 0.0), "high": mid + half,
                       "holiday": is_holiday(day), "rain": p})
    return {"method": "diario", "sigma": sigma, "level": level, "dow": dow, "holiday_factor": holiday,
            "points": points}


def backtest_daily(history: dict[date, float], first_day: date, k: int = 14) -> float | None:
    cut = first_day - timedelta(days=k)
    result = forecast_daily(history, cut, k)
    if result is None:
        return None
    actual = [history.get(p["date"], 0) for p in result["points"]]
    return mape(actual, [p["expected"] for p in result["points"]])
