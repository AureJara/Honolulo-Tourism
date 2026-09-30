from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import ProviderFetchLog, WeatherObservation
from conftest import hour_ago, make_reading

API = "/api/v1/weather"


def backdate(app, minutes):
    """Envejece lo ya guardado (lecturas y bitácora) para simular el paso del tiempo."""
    with app.app_context():
        delta = timedelta(minutes=minutes)
        for row in db.session.execute(db.select(WeatherObservation)).scalars():
            row.fetched_at = row.fetched_at - delta
        for row in db.session.execute(db.select(ProviderFetchLog)).scalars():
            row.fetched_at = row.fetched_at - delta
        db.session.commit()


@pytest.mark.parametrize("method,path", [
    ("get", f"{API}/current"), ("post", f"{API}/refresh"), ("get", f"{API}/history"),
    ("get", f"{API}/location"), ("get", f"{API}/status"),
])
def test_endpoints_require_session(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401 and resp.get_json()["code"] == "AUTH_REQUIRED"


def test_location_is_honolulo_with_coordinates(client, headers):
    body = client.get(f"{API}/location", headers=headers).get_json()
    assert body["name"] == "Honolulo" and body["province"] == "Leoncio Prado"
    assert body["department"] == "Huánuco" and body["country"] == "Perú"
    assert body["latitude"] == pytest.approx(-9.295556) and body["timezone"] == "America/Lima"


def test_current_fetches_stores_and_follows_contract(client, headers, provider):
    resp = client.get(f"{API}/current", headers=headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["location"] == "Honolulo"
    assert body["updated_at"].endswith("-05:00")            # RN04: fecha y hora de obtención, hora de Lima
    w = body["weather"]
    assert w["temperature"] == 26.4 and w["feels_like"] == 27.1
    assert w["humidity"] == 78 and w["precipitation"] == 0.4 and w["rain_probability"] == 35
    assert w["wind_speed"] == 6.2 and w["cloud_cover"] == 64
    assert w["condition"] == "Parcialmente nublado" and w["icon"] == "partly_cloudy_day"
    assert body["stale"] is False and body["age_seconds"] >= 0
    assert provider.calls == ["current"]


def test_current_within_max_age_does_not_call_provider(client, headers, provider):
    client.get(f"{API}/current", headers=headers)
    client.get(f"{API}/current", headers=headers)
    assert provider.calls == ["current"]


def test_current_uses_night_variant(client, headers, provider):
    provider.current = make_reading(code=0, is_day=False)
    weather = client.get(f"{API}/current", headers=headers).get_json()["weather"]
    assert weather["condition"] == "Despejado" and weather["icon"] == "nights_stay"


def test_stale_data_is_flagged_when_provider_fails(app, client, headers, provider):
    """RN08: se muestra el último dato guardado indicando que es anterior."""
    client.get(f"{API}/current", headers=headers)
    backdate(app, 120)
    provider.fail = True
    body = client.get(f"{API}/current", headers=headers).get_json()
    assert body["stale"] is True
    assert body["message"] == "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."
    assert body["age_seconds"] >= 7200
    assert body["weather"]["temperature"] == 26.4


def test_no_data_and_provider_down_is_503(client, headers, provider):
    """Escenario 5: sin dato confirmable no se presenta nada como actual."""
    provider.fail = True
    resp = client.get(f"{API}/current", headers=headers)
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["code"] == "WEATHER_UNAVAILABLE" and "no está disponible" in body["detail"]
    assert "weather" not in body


def test_same_observation_is_upserted_not_duplicated(app, client, headers, provider):
    client.get(f"{API}/current", headers=headers)
    backdate(app, 120)
    provider.current = make_reading(when=provider.current.time_utc, temp=27.0)
    client.get(f"{API}/current", headers=headers)
    with app.app_context():
        rows = db.session.execute(db.select(WeatherObservation)).scalars().all()
    assert len(rows) == 1 and rows[0].temperature_c == 27.0


def test_manual_refresh_respects_minimum_interval(client, headers, provider):
    first = client.post(f"{API}/refresh", headers=headers).get_json()
    assert first["refreshed"] is True and first["stale"] is False
    second = client.post(f"{API}/refresh", headers=headers).get_json()
    assert second["refreshed"] is False and second["reason"] == "recent"
    assert second["next_allowed_at"]
    assert provider.calls == ["current"]


def test_manual_refresh_after_interval_calls_provider_again(app, client, headers, provider):
    client.post(f"{API}/refresh", headers=headers)
    backdate(app, 5)
    provider.current = make_reading(when=provider.current.time_utc + timedelta(minutes=15), temp=28.0)
    body = client.post(f"{API}/refresh", headers=headers).get_json()
    assert body["refreshed"] is True and body["weather"]["temperature"] == 28.0


def test_manual_refresh_failure_returns_last_known_and_does_not_hammer_provider(app, client, headers, provider):
    client.post(f"{API}/refresh", headers=headers)
    backdate(app, 5)
    provider.fail = True
    resp = client.post(f"{API}/refresh", headers=headers)
    assert resp.status_code == 502
    body = resp.get_json()
    assert body["code"] == "WEATHER_PROVIDER_UNAVAILABLE"
    assert body["detail"] == "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."
    assert body["last_known"]["stale"] is True
    calls = len(provider.calls)
    assert client.post(f"{API}/refresh", headers=headers).status_code == 502
    assert len(provider.calls) == calls            # el fallo reciente evita insistir


def test_history_lists_records_with_local_date_time_and_paginates(app, client, headers, provider):
    with app.app_context():
        for i in range(5):
            when = datetime(2026, 9, 30, 14 + i, 0, tzinfo=timezone.utc)  # 09:00..13:00 en Lima
            db.session.add(WeatherObservation(
                location_code="honolulo", observed_at=when, temperature_c=20 + i, humidity_pct=70,
                precipitation_mm=0, condition="clear", source="current", provider="open-meteo",
                fetched_at=when))
        db.session.add(WeatherObservation(
            location_code="honolulo", observed_at=datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc),
            temperature_c=18, precipitation_mm=0, condition="rain", source="hourly",
            provider="open-meteo", fetched_at=datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)))
        db.session.commit()

    page1 = client.get(f"{API}/history?page_size=2", headers=headers).get_json()
    assert page1["total"] == 6 and len(page1["items"]) == 2
    first = page1["items"][0]
    assert first["date"] == "2026-09-30" and first["time"] == "13:00" and first["temperature"] == 24
    assert {"id", "location", "humidity", "precipitation", "rain_probability", "wind_speed",
            "cloud_cover", "condition"} <= set(first)

    only_current = client.get(f"{API}/history?source=current&from=2026-09-30&to=2026-09-30", headers=headers).get_json()
    assert only_current["total"] == 5
    assert client.get(f"{API}/history?source=hourly", headers=headers).get_json()["total"] == 1


@pytest.mark.parametrize("query", ["page=0", "page_size=500", "page=x", "from=30-09-2026",
                                   "from=2026-10-02&to=2026-10-01", "source=otra"])
def test_history_validates_params(client, headers, query):
    assert client.get(f"{API}/history?{query}", headers=headers).status_code == 422


# ----------------------------------------------------- serie horaria / interno
def test_internal_endpoint_requires_internal_token(client, headers):
    url = "/internal/observations?from=2026-09-30T00:00:00Z&to=2026-09-30T05:00:00Z"
    assert client.get(url).status_code == 403
    assert client.get(url, headers=headers).status_code == 403    # un JWT de usuario no basta


def test_sync_hourly_keeps_only_completed_hours_and_is_idempotent(app, client, internal, provider):
    provider.hourly = [make_reading(when=hour_ago(3), temp=21.0), make_reading(when=hour_ago(2), temp=22.0),
                       make_reading(when=hour_ago(0) + timedelta(hours=5), temp=30.0)]      # futura
    from app import service
    with app.app_context():
        assert service.sync_hourly(3) == 2
        assert service.sync_hourly(3) == 2           # upsert: no duplica filas
        rows = db.session.execute(db.select(WeatherObservation)).scalars().all()
    assert len(rows) == 2 and all(r.source == "hourly" for r in rows)


def test_internal_observations_ensure_triggers_sync_once(client, internal, provider):
    provider.hourly = [make_reading(when=hour_ago(h), temp=20.0 + h) for h in (5, 4, 3, 2)]
    start, end = hour_ago(5), hour_ago(2)
    url = f"/internal/observations?from={start.isoformat()}&to={end.isoformat()}&ensure=1".replace("+00:00", "Z")
    body = client.get(url, headers=internal).get_json()
    assert [i["temperature"] for i in body["items"]] == [25.0, 24.0, 23.0, 22.0]
    assert body["items"][0]["observed_at"].endswith("Z")
    assert provider.calls == [("hourly", 1)]

    client.get(url, headers=internal)                # ya hay todo: no se vuelve a consultar
    assert provider.calls == [("hourly", 1)]


def test_internal_observations_ensure_is_debounced_when_provider_fails(client, internal, provider):
    provider.fail = True
    start, end = hour_ago(5), hour_ago(3)
    url = f"/internal/observations?from={start.isoformat()}&to={end.isoformat()}&ensure=1".replace("+00:00", "Z")
    assert client.get(url, headers=internal).get_json()["items"] == []
    client.get(url, headers=internal)
    assert len(provider.calls) == 1


def test_internal_observations_validates_range(client, internal):
    bad = "/internal/observations?from=2026-09-30T00:00:00Z&to=2026-08-01T00:00:00Z"
    assert client.get(bad, headers=internal).status_code == 422
    assert client.get("/internal/observations", headers=internal).status_code == 422
