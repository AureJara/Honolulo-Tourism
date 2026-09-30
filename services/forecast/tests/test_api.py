from datetime import timedelta

import pytest

from conftest import headers, local_today, lima_hour, now_utc, seed_day  # noqa: F401

API = "/api/v1/weather"


@pytest.mark.parametrize("method,path", [
    ("get", f"{API}/forecast?date=2026-10-01"), ("get", f"{API}/calendar?month=2026-10"),
    ("get", f"{API}/evaluation/1"), ("get", f"{API}/evaluation?date=2026-10-01"),
    ("post", f"{API}/forecast/refresh"), ("get", f"{API}/scoring-parameters"),
])
def test_endpoints_require_session(client, method, path):
    """RF01: sin sesión activa no hay acceso al módulo meteorológico."""
    resp = getattr(client, method)(path)
    assert resp.status_code == 401 and resp.get_json()["code"] == "AUTH_REQUIRED"


def test_forecast_for_a_day_follows_contract_and_design_panel(app, client, headers):
    day = local_today() + timedelta(days=3)
    seed_day(app, day, now_utc() - timedelta(hours=2), humidity=75, rain_prob=15, precip=0.0, wind=6.0)
    body = client.get(f"{API}/forecast?date={day.isoformat()}", headers=headers).get_json()

    assert body["location"] == "Honolulo" and body["date"] == day.isoformat() and body["available"] is True
    assert len(body["forecast"]) == 10
    first = body["forecast"][0]
    assert first["time"] == "08:00" and first["temperature"] == 22 and first["humidity"] == 75
    assert first["rain_probability"] == 15 and first["precipitation"] == 0 and first["condition"] == "Neblina"
    assert isinstance(first["forecast_id"], int) and first["evaluation"] == {"status": "pending"}

    # Panel del diseño: temperatura máxima, franjas, resumen y métricas
    assert body["temperature"] == 26 and body["condition_code"] == "clear"
    assert body["summary"] == "Soleado con niebla matinal templada"
    assert [(b["from"], b["to"], b["temperature"], b["label"]) for b in body["bands"]] == [
        ("08:00", "12:00", 22, "Bruma fresca"), ("12:00", "16:00", 26, "Sol radiante"),
        ("16:00", "18:00", 24, "Nublado")]
    assert body["metrics"]["rain_probability"] == {"value": 15, "level": "low", "label": "Baja"}
    assert body["metrics"]["humidity"]["label"] == "Óptima"
    assert body["issued_at"].endswith("-05:00")


def test_future_forecast_is_pending_evaluation_without_invented_score(app, client, headers):
    """RN07: sin observación no se muestra puntuación."""
    day = local_today() + timedelta(days=2)
    seed_day(app, day, now_utc() - timedelta(hours=1))
    ev = client.get(f"{API}/forecast?date={day.isoformat()}", headers=headers).get_json()["evaluation"]
    assert ev == {"status": "pending", "accuracy_score": None, "hours_evaluated": 0, "hours_total": 10,
                  "message": "Pronóstico pendiente de evaluación."}


def test_day_without_forecast_reports_unavailable(client, headers):
    future = (local_today() + timedelta(days=30)).isoformat()
    past = (local_today() - timedelta(days=30)).isoformat()
    fut = client.get(f"{API}/forecast?date={future}", headers=headers).get_json()
    assert fut["available"] is False and fut["forecast"] == [] and "Aún no hay pronóstico" in fut["message"]
    old = client.get(f"{API}/forecast?date={past}", headers=headers).get_json()
    assert old["available"] is False and "No se guardó pronóstico" in old["message"]


@pytest.mark.parametrize("query", ["", "date=", "date=01/10/2026", "date=2026-13-40", "date=1999-01-01"])
def test_forecast_validates_date(client, headers, query):
    assert client.get(f"{API}/forecast?{query}", headers=headers).status_code == 422


def test_calendar_marks_days_with_data(app, client, headers):
    day = local_today() + timedelta(days=3)
    seed_day(app, day, now_utc() - timedelta(hours=1))
    body = client.get(f"{API}/calendar?month={day.strftime('%Y-%m')}", headers=headers).get_json()
    assert body["location"] == "Honolulo" and body["timezone"] == "America/Lima"
    assert body["month"] == day.strftime("%Y-%m")
    by_date = {d["date"]: d for d in body["days"]}
    assert len(body["days"]) == len({d["date"] for d in body["days"]}) >= 28
    cell = by_date[day.isoformat()]
    assert cell["available"] is True and cell["temperature"] == 26
    assert cell["icon"] == "wb_sunny" and cell["condition"] == "Soleado"
    assert sum(1 for d in body["days"] if d["available"]) == 1
    empty = next(d for d in body["days"] if not d["available"])
    assert set(empty) == {"date", "available"}                # sin datos: no se inventa nada (RN03)
    assert body["updated_at"].endswith("-05:00")


@pytest.mark.parametrize("month", ["", "2026-13", "2026-1", "octubre", "1999-01"])
def test_calendar_validates_month(client, headers, month):
    assert client.get(f"{API}/calendar?month={month}", headers=headers).status_code == 422


def test_calendar_february_and_december_lengths(client, headers):
    feb = client.get(f"{API}/calendar?month=2027-02", headers=headers).get_json()
    assert len(feb["days"]) == 28
    dec = client.get(f"{API}/calendar?month=2026-12", headers=headers).get_json()
    assert len(dec["days"]) == 31 and dec["days"][-1]["date"] == "2026-12-31"


def test_calendar_uses_rain_when_it_rains_at_any_hour(app, client, headers):
    day = local_today() + timedelta(days=5)
    pattern = [(22, "clear")] * 9 + [(21, "rain")]
    seed_day(app, day, now_utc() - timedelta(hours=1), pattern=pattern)
    cell = next(d for d in client.get(f"{API}/calendar?month={day.strftime('%Y-%m')}", headers=headers)
                .get_json()["days"] if d["date"] == day.isoformat())
    assert cell["condition_code"] == "rain" and cell["icon"] == "rainy"
