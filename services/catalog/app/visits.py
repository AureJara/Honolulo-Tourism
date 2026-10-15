"""Visitas por lugar: registro, resumen por día/mes/año, predicción y datos de demostración.

El modelo matemático está en ``visit_model`` (funciones puras); aquí se leen y agregan los datos y se arma la respuesta.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa

from honolulo_common.errors import ApiError

from . import service
from . import visit_model as vm
from .extensions import db
from .models import Place, PlaceVisit

PERU = timezone(timedelta(hours=-5))                    # Perú no usa horario de verano
MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
GRANULARITIES = ("day", "month", "year")


def today_pe() -> date:
    return datetime.now(PERU).date()


# ----------------------------------------------------------------- datos de demostración
DEMO_BASE = {"catarata-velo-de-las-ninfas": 70, "cascada-san-miguel": 55, "catarata-santa-carmen": 40,
             "catarata-gloriapata": 32}
DEMO_DEFAULT = 35
DEMO_YEARS = 3
DEMO_DOW = (0.75, 0.70, 0.72, 0.80, 1.10, 1.55, 1.45)                       # lunes … domingo
DEMO_TOURISM = (0.95, 0.85, 0.95, 1.10, 1.00, 1.00, 1.25, 1.20, 1.00, 1.00, 0.95, 1.10)   # vacaciones, lluvias, etc.


def demo_value(slug: str, day: date, start: date) -> int:
    """Valor de demostración reproducible: depende solo del lugar y de la fecha."""
    rng = random.Random(f"{slug}:{day.isoformat()}")
    pascua = vm.easter(day.year)
    base = DEMO_BASE.get(slug, DEMO_DEFAULT)
    value = base * vm.regional_seasonal_index()[day.month - 1] * DEMO_TOURISM[day.month - 1]
    value *= 1 + 0.06 * ((day - start).days / 365)                          # crecimiento de ~6 % al año
    value *= DEMO_DOW[day.weekday()]
    value *= 1.45 if vm.is_holiday(day) else 1.0
    value *= 1.25 if pascua - timedelta(days=4) <= day <= pascua + timedelta(days=1) else 1.0   # Semana Santa
    value *= rng.lognormvariate(0, 0.17)
    value *= 0.6 if rng.random() < 0.12 else 1.0                            # día de mal tiempo
    return max(int(round(value)), 0)


def seed_demo_visits(today: date | None = None) -> int:
    """Completa los días que falten de los últimos ``DEMO_YEARS`` años con datos de demostración. Idempotente."""
    today = today or today_pe()
    start = today - timedelta(days=365 * DEMO_YEARS)
    created = 0
    for place in db.session.execute(db.select(Place)).scalars():
        have = set(db.session.execute(
            sa.select(PlaceVisit.visit_date).where(PlaceVisit.place_id == place.id, PlaceVisit.visit_date >= start)
        ).scalars())
        rows = [{"place_id": place.id, "visit_date": start + timedelta(days=i),
                 "visitors": demo_value(place.slug, start + timedelta(days=i), start), "source": "demo"}
                for i in range((today - start).days) if (start + timedelta(days=i)) not in have]
        for i in range(0, len(rows), 1000):
            db.session.execute(sa.insert(PlaceVisit), rows[i:i + 1000])
        created += len(rows)
    db.session.commit()
    return created


def delete_demo(slug: str | None = None) -> int:
    stmt = sa.delete(PlaceVisit).where(PlaceVisit.source == "demo")
    if slug:
        stmt = stmt.where(PlaceVisit.place_id == sa.select(Place.id).where(Place.slug == slug).scalar_subquery())
    count = db.session.execute(stmt).rowcount
    db.session.commit()
    return count


# ----------------------------------------------------------------- registro
def record(slug: str, day: date, visitors: int, user_id: str, today: date | None = None) -> dict:
    today = today or today_pe()
    if day > today:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": "date", "message": "No se pueden registrar visitas de una fecha futura."}])
    place = db.session.execute(db.select(Place).where(Place.slug == slug)).scalar_one_or_none()
    if place is None:
        raise ApiError(404, "NOT_FOUND", "Lugar no encontrado.")
    row = db.session.execute(db.select(PlaceVisit).where(
        PlaceVisit.place_id == place.id, PlaceVisit.visit_date == day)).scalar_one_or_none()
    replaced_demo = bool(row and row.source == "demo")
    if row is None:
        row = PlaceVisit(place_id=place.id, visit_date=day)
        db.session.add(row)
    row.visitors, row.source, row.recorded_by = visitors, "registro", user_id
    db.session.commit()
    return {"place": place.slug, "place_name": place.name, "date": day.isoformat(), "visitors": row.visitors,
            "source": row.source, "replaced_demo": replaced_demo}


def delete_record(slug: str, day: date) -> None:
    """Elimina un registro anotado por el personal (para corregir un error). Los datos de demostración no se tocan."""
    place = db.session.execute(db.select(Place).where(Place.slug == slug)).scalar_one_or_none()
    count = 0
    if place is not None:
        count = db.session.execute(sa.delete(PlaceVisit).where(
            PlaceVisit.place_id == place.id, PlaceVisit.visit_date == day, PlaceVisit.source == "registro")).rowcount
        db.session.commit()
    if not count:
        raise ApiError(404, "NOT_FOUND", "No hay un registro de visitas para ese lugar y esa fecha.")


def recent_records(limit: int = 12) -> list[dict]:
    rows = db.session.execute(
        sa.select(PlaceVisit, Place.slug, Place.name).join(Place, Place.id == PlaceVisit.place_id)
        .where(PlaceVisit.source == "registro").order_by(PlaceVisit.updated_at.desc()).limit(limit)).all()
    return [{"place": slug, "place_name": name, "date": v.visit_date.isoformat(), "visitors": v.visitors}
            for v, slug, name in rows]


# ----------------------------------------------------------------- lectura y agregación
def _scope(slug: str | None) -> tuple[list, dict | None]:
    if slug:
        place = service.get_public(slug)
        return [place.id], {"slug": place.slug, "name": place.name}
    places = service.list_places(published_only=True)
    return [p.id for p in places], None


def _totals(ids: list) -> dict[date, tuple[int, int]]:
    """fecha → (visitantes, de los cuales de demostración)."""
    if not ids:
        return {}
    demo = sa.func.sum(sa.case((PlaceVisit.source == "demo", PlaceVisit.visitors), else_=0))
    rows = db.session.execute(
        sa.select(PlaceVisit.visit_date, sa.func.sum(PlaceVisit.visitors), demo)
        .where(PlaceVisit.place_id.in_(ids)).group_by(PlaceVisit.visit_date)).all()
    return {d: (int(v), int(dm or 0)) for d, v, dm in rows}


def _source(visitors: int, demo: int) -> str:
    return "demo" if demo and demo >= visitors else ("registro" if not demo else "mixto")


def _quality(totals: dict[date, tuple[int, int]]) -> dict:
    demo_days = sum(1 for v, dm in totals.values() if dm and dm >= v)
    mixed_days = sum(1 for v, dm in totals.values() if dm and dm < v)
    real_days = len(totals) - demo_days - mixed_days
    return {"has_demo": demo_days + mixed_days > 0, "has_real": real_days + mixed_days > 0,
            "demo_days": demo_days, "real_days": real_days, "mixed_days": mixed_days,
            "first_date": min(totals).isoformat() if totals else None,
            "last_date": max(totals).isoformat() if totals else None}


def _pack(point: dict) -> dict:
    return {"expected": int(round(point["expected"])), "low": int(round(point["low"])),
            "high": int(round(point["high"]))}


def _sum_points(points: list[dict], actual: float = 0.0) -> dict:
    half = sum(((p["high"] - p["low"]) / 2) ** 2 for p in points) ** 0.5
    total = actual + sum(p["expected"] for p in points)
    return {"expected": total, "low": max(total - half, 0.0), "high": total + half}


def _month_key(y: int, m: int) -> str:
    return f"{y}-{m:02d}"


def stats(granularity: str, slug: str | None = None, rain: dict[date, float] | None = None,
          today: date | None = None) -> dict:
    if granularity not in GRANULARITIES:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los parámetros.",
                       errors=[{"field": "granularity", "message": "Usa day, month o year."}])
    today = today or today_pe()
    ids, place = _scope(slug)
    totals = _totals(ids)
    hist = {d: float(v) for d, (v, _) in totals.items()}
    past = {d: v for d, v in hist.items() if d < today}

    month_index, index_source = vm.monthly_index_from(past) if past else (list(vm.regional_seasonal_index()), "regional")
    daily_fc = vm.forecast_daily(hist, today, 30, rain, month_index)

    # meses completos consecutivos (se descarta el primero si empezó a mitad de mes y el mes en curso)
    by_month: dict[tuple[int, int], list[int]] = {}
    first_day: dict[tuple[int, int], date] = {}
    for d, (v, dm) in totals.items():
        acc = by_month.setdefault((d.year, d.month), [0, 0])
        acc[0] += v
        acc[1] += dm
        first_day[(d.year, d.month)] = min(d, first_day.get((d.year, d.month), d))
    cur = (today.year, today.month)
    keys = sorted(k for k in by_month if k < cur)
    if keys and first_day[keys[0]].day > 1:
        keys = keys[1:]
    y_vals: list[float] = []
    first_month = 1
    if keys:
        first_month = keys[0][1]
        prev = vm.month_add(cur[0], cur[1], -1)
        n_months = (prev[0] - keys[0][0]) * 12 + prev[1] - keys[0][1] + 1
        y_vals = vm.fill_gaps([float(by_month[vm.month_add(keys[0][0], keys[0][1], i)][0])
                               if vm.month_add(keys[0][0], keys[0][1], i) in by_month else None
                               for i in range(n_months)])
    month_fc = vm.forecast_monthly(y_vals, first_month, 24) if y_vals else None

    series: list[dict] = []
    forecast: list[dict] = []
    model: dict
    if granularity == "day":
        for i in range(59, -1, -1):
            d = today - timedelta(days=i)
            v, dm = totals.get(d, (None, 0))
            series.append({"period": d.isoformat(), "visitors": v, "source": _source(v, dm) if v is not None else None,
                           "partial": d == today, "holiday": vm.is_holiday(d)})
        if daily_fc:
            for p in daily_fc["points"][:14]:
                forecast.append({"period": p["date"].isoformat(), **_pack(p), "holiday": p["holiday"], "rain": p["rain"]})
        mape = vm.backtest_daily(past, today) if daily_fc else None
        model = _model_info("diario", daily_fc, mape, index_source, rain)
    elif granularity == "month":
        for i in range(35, -1, -1):
            y, m = vm.month_add(cur[0], cur[1], -i)
            acc = by_month.get((y, m))
            series.append({"period": _month_key(y, m), "visitors": acc[0] if acc else None,
                           "source": _source(*acc) if acc else None, "partial": (y, m) == cur})
        if month_fc:
            for h, p in enumerate(month_fc["points"][:12]):
                y, m = vm.month_add(cur[0], cur[1], h)
                forecast.append({"period": _month_key(y, m), **_pack(p)})
        model = _model_info(month_fc["method"] if month_fc else None, month_fc,
                            vm.backtest_monthly(y_vals, first_month) if y_vals else None, index_source, None)
    else:
        years = sorted({y for y, _ in by_month})
        for y in years:
            months = [by_month[(y, m)] for m in range(1, 13) if (y, m) in by_month]
            total, dm = sum(a[0] for a in months), sum(a[1] for a in months)
            full = all((y, m) in by_month for m in range(1, 13)) and y < today.year and first_day.get((y, 1), date(y, 1, 1)).day == 1
            series.append({"period": str(y), "visitors": total, "source": _source(total, dm), "partial": not full,
                           "months": len(months)})
        if month_fc:
            ahead = 13 - cur[1]                                                # meses que faltan del año en curso
            done = sum(by_month[(today.year, m)][0] for m in range(1, cur[1]) if (today.year, m) in by_month)
            forecast.append({"period": str(today.year), **_pack(_sum_points(month_fc["points"][:ahead], done))})
            forecast.append({"period": str(today.year + 1), **_pack(_sum_points(month_fc["points"][ahead:ahead + 12]))})
        model = _model_info(month_fc["method"] if month_fc else None, month_fc,
                            vm.backtest_monthly(y_vals, first_month) if y_vals else None, index_source, None)

    quality = _quality(totals)
    if quality["has_demo"] and model.get("method"):
        model["notes"].append("Las cifras incluyen datos de DEMOSTRACIÓN: el error de validación se mide sobre ellos y "
                              "será mayor con visitas reales.")
    kpis = {
        "last_7": int(sum(hist.get(today - timedelta(days=i), 0) for i in range(1, 8))),
        "last_30": int(sum(hist.get(today - timedelta(days=i), 0) for i in range(1, 31))),
        "year_to_date": int(sum(v for d, v in hist.items() if d.year == today.year and d <= today)),
        "next_7": int(round(sum(p["expected"] for p in daily_fc["points"][:7]))) if daily_fc else None,
        "next_30": int(round(sum(p["expected"] for p in daily_fc["points"][:30]))) if daily_fc else None,
    }
    return {"granularity": granularity, "place": place, "today": today.isoformat(), "series": series,
            "forecast": forecast, "kpis": kpis, "model": model, "quality": quality}


def _model_info(method: str | None, result: dict | None, mape: float | None, index_source: str,
                rain: dict | None) -> dict:
    if result is None:
        return {"method": None, "label": None, "backtest_mape": None, "notes": [],
                "message": "Aún no hay suficiente historial para predecir (se necesitan al menos 4 semanas de visitas "
                           "para el pronóstico diario y 3 meses completos para el mensual y anual)."}
    labels = {"diario": "Perfil semanal × estacionalidad mensual × feriados",
              "holt-winters": "Holt-Winters multiplicativo (nivel, tendencia y estacionalidad anual)",
              "regional": "Nivel reciente × estacionalidad regional de Huánuco (BCRP/MINCETUR)"}
    notes = []
    if method == "diario":
        notes.append(f"Estacionalidad mensual: índice {'propio' if index_source == 'propio' else 'regional (BCRP) por falta de dos años de datos'}.")
        notes.append("El efecto de la lluvia es un supuesto (−30 % con 100 % de probabilidad), no un valor aprendido.")
        if rain:
            notes.append("Los días con pronóstico de lluvia ya están ajustados.")
    elif method == "regional":
        notes.append("Hay menos de dos años de datos propios: se usa la estacionalidad regional como respaldo.")
    return {"method": method, "label": labels.get(method), "backtest_mape": mape,
            "interval": "95 %", "notes": notes}


# ----------------------------------------------------------------- referencia regional
def regional_context() -> dict:
    data = vm.regional()
    monthly = data["monthly"]
    years = sorted({int(k[:4]) for k in monthly})
    return {
        "source": data["source"], "series_code": data["series_code"], "series_name": data["series_name"],
        "url": data["url"], "unit": data["unit"], "note": data["note"], "retrieved": data["retrieved"],
        "monthly": [{"period": k, "arrivals": v} for k, v in sorted(monthly.items())],
        "yearly": [{"year": y, "arrivals": sum(v for k, v in monthly.items() if k.startswith(str(y)))} for y in years],
        "seasonal_index": [{"month": MONTHS_ES[i], "index": v} for i, v in enumerate(vm.regional_seasonal_index())],
    }


