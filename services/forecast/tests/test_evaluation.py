from datetime import timedelta

import pytest
import responses

from app import service
from app.extensions import db
from app.models import ForecastEvaluation
from conftest import (admin_headers, headers, lima_hour, local_today, mock_observations,  # noqa: F401
                      now_utc, seed_day)

API = "/api/v1/weather"

OBSERVED = {"temperature": 25.0, "humidity": 78, "precipitation": 0.0, "wind_speed": 5.0,
            "condition_code": "clear", "rain_probability": None}


def past_day_ids(app, **kw):
    """Día de ayer con un pronóstico emitido mucho antes (antelación suficiente para evaluar)."""
    day = local_today() - timedelta(days=1)
    return day, seed_day(app, day, lima_hour(day, 0) - timedelta(hours=30), humidity=75, rain_prob=40,
                         precip=0.0, wind=6.0, **kw)


@responses.activate
def test_scenario3_evaluation_compares_forecast_with_observation(app, client, headers):
    day, ids = past_day_ids(app)
    calls = mock_observations(lambda hour: OBSERVED)
    body = client.get(f"{API}/evaluation/{ids[0]}", headers=headers).get_json()

    assert body["location"] == "Honolulo" and body["forecast_id"] == ids[0]
    assert body["status"] == "evaluated"
    v = body["variables"]
    # 08:00 → previsto 22 °C y 75 %, observado 25 °C y 78 %
    assert v["temperature"] == {"predicted": 22.0, "observed": 25.0, "error": 3.0, "score": 0.4}
    assert v["humidity"] == {"predicted": 75, "observed": 78, "error": 3, "score": 0.9}
    assert v["rain"] == {"predicted": False, "observed": False, "correct": True, "probability": 40, "score": 0.84}
    assert v["condition"]["predicted"] == "Neblina" and v["condition"]["observed"] == "Soleado"
    assert 0 <= body["accuracy_score"] <= 100 and body["accuracy_label"]
    assert body["params_version"] == 1 and len(calls) == 1


@responses.activate
def test_evaluation_is_stored_and_not_recomputed(app, client, headers):
    day, ids = past_day_ids(app)
    calls = mock_observations(lambda hour: OBSERVED)
    first = client.get(f"{API}/evaluation/{ids[2]}", headers=headers).get_json()
    second = client.get(f"{API}/evaluation/{ids[2]}", headers=headers).get_json()
    assert first["accuracy_score"] == second["accuracy_score"] and len(calls) == 1
    with app.app_context():
        assert db.session.execute(db.select(db.func.count()).select_from(ForecastEvaluation)).scalar_one() == 1


@responses.activate
def test_scenario4_future_forecast_is_pending_and_never_asks_for_observations(app, client, headers):
    day = local_today() + timedelta(days=1)
    ids = seed_day(app, day, now_utc() - timedelta(hours=1))
    calls = mock_observations(lambda hour: OBSERVED)
    body = client.get(f"{API}/evaluation/{ids[0]}", headers=headers).get_json()
    assert body["status"] == "pending" and body["accuracy_score"] is None
    assert body["message"] == "Pronóstico pendiente de evaluación."
    assert "variables" not in body and calls == []


@responses.activate
def test_missing_observation_keeps_forecast_pending(app, client, headers):
    day, ids = past_day_ids(app)
    mock_observations(lambda hour: None)
    body = client.get(f"{API}/evaluation/{ids[0]}", headers=headers).get_json()
    assert body["status"] == "pending" and body["reason"] == "observation_missing"
    assert body["message"] == "Pronóstico pendiente de evaluación."


@responses.activate
def test_weather_service_down_keeps_forecast_pending(app, client, headers):
    day, ids = past_day_ids(app)
    mock_observations(lambda hour: OBSERVED, status=503)
    body = client.get(f"{API}/evaluation/{ids[0]}", headers=headers).get_json()
    assert body["status"] == "pending" and body["reason"] == "observation_unavailable"


def test_unknown_forecast_is_404(client, headers):
    resp = client.get(f"{API}/evaluation/99999", headers=headers)
    assert resp.status_code == 404 and resp.get_json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize("forecast_id", ["9223372036854775808", "9" * 40, "0", "99999999999999999999999999999"])
def test_out_of_range_forecast_ids_are_404_not_500(client, headers, forecast_id):
    """Regresión QA D1: un id que no cabe en bigint provocaba una excepción de base de datos (500)."""
    resp = client.get(f"{API}/evaluation/{forecast_id}", headers=headers)
    assert resp.status_code == 404 and resp.get_json()["code"] == "NOT_FOUND"


@responses.activate
def test_day_evaluation_averages_hours_and_feeds_the_day_panel(app, client, headers):
    day, ids = past_day_ids(app)
    calls = mock_observations(lambda hour: OBSERVED)
    summary = client.get(f"{API}/evaluation?date={day.isoformat()}", headers=headers).get_json()
    assert summary["status"] == "evaluated" and summary["hours_evaluated"] == summary["hours_total"] == 10
    assert 0 < summary["accuracy_score"] < 100 and summary["accuracy_label"]
    assert len(calls) == 1                                           # una sola consulta para todo el día

    panel = client.get(f"{API}/forecast?date={day.isoformat()}", headers=headers).get_json()
    assert panel["evaluation"]["status"] == "evaluated"
    assert all(e["evaluation"]["status"] == "evaluated" for e in panel["forecast"])


@responses.activate
def test_day_evaluation_is_partial_when_some_hours_are_missing(app, client, headers):
    day, ids = past_day_ids(app)
    early = {lima_hour(day, h) for h in range(8, 12)}
    mock_observations(lambda hour: OBSERVED if hour in early else None)
    summary = client.get(f"{API}/evaluation?date={day.isoformat()}", headers=headers).get_json()
    assert summary["status"] == "partial" and summary["hours_evaluated"] == 4 and summary["hours_total"] == 10


@responses.activate
def test_day_evaluation_pending_without_observations(app, client, headers):
    day, ids = past_day_ids(app)
    mock_observations(lambda hour: None)
    summary = client.get(f"{API}/evaluation?date={day.isoformat()}", headers=headers).get_json()
    assert summary["status"] == "pending" and summary["accuracy_score"] is None
    assert summary["message"] == "Pronóstico pendiente de evaluación."


@responses.activate
def test_evaluate_pending_job_evaluates_overdue_reference_forecasts_once(app, provider):
    day, ids = past_day_ids(app)
    calls = mock_observations(lambda hour: OBSERVED)
    with app.app_context():
        assert service.evaluate_pending() == 10
        assert service.evaluate_pending() == 0                      # idempotente
    assert len(calls) == 1


@responses.activate
def test_evaluation_request_carries_internal_token(app, client, headers):
    day, ids = past_day_ids(app)
    calls = mock_observations(lambda hour: OBSERVED)
    client.get(f"{API}/evaluation/{ids[0]}", headers=headers)
    assert calls[0].headers["X-Internal-Token"] == "internal-test"


# ------------------------------------------------ parámetros de puntuación (RF11)
def test_scoring_parameters_default_and_only_admin_can_change(client, headers, admin_headers):
    current = client.get(f"{API}/scoring-parameters", headers=headers).get_json()
    assert current["version"] == 1 and current["is_active"] is True
    assert abs(sum(current["weights"].values()) - 1.0) < 1e-9

    payload = {**current, "weights": {"temperature": 1.0}}
    assert client.put(f"{API}/scoring-parameters", json=payload, headers=headers).status_code == 403
    bad = client.put(f"{API}/scoring-parameters", json={**payload, "weights": {"viento": 1}}, headers=admin_headers)
    assert bad.status_code == 422
    ok = client.put(f"{API}/scoring-parameters", json=payload, headers=admin_headers)
    assert ok.status_code == 201 and ok.get_json()["version"] == 2
    assert client.get(f"{API}/scoring-parameters", headers=headers).get_json()["version"] == 2


@responses.activate
def test_changing_the_formula_keeps_old_evaluations_and_creates_new_ones(app, client, headers, admin_headers):
    day, ids = past_day_ids(app)
    mock_observations(lambda hour: OBSERVED)
    v1 = client.get(f"{API}/evaluation/{ids[0]}", headers=headers).get_json()
    params = client.get(f"{API}/scoring-parameters", headers=headers).get_json()
    client.put(f"{API}/scoring-parameters", json={**params, "weights": {"temperature": 1.0}}, headers=admin_headers)

    v2 = client.get(f"{API}/evaluation/{ids[0]}", headers=headers).get_json()
    assert v1["params_version"] == 1 and v2["params_version"] == 2
    assert v2["accuracy_score"] == 40.0                               # solo temperatura: error 3 °C de 5 °C → 0.4
    with app.app_context():
        assert db.session.execute(db.select(db.func.count()).select_from(ForecastEvaluation)).scalar_one() == 2
