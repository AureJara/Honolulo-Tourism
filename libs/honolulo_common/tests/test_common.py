from datetime import date, datetime, timedelta, timezone

import jwt
import pytest
import responses
from flask import Flask, jsonify

from honolulo_common.conditions import condition_code_from_wmo, describe
from honolulo_common.errors import ApiError, register_error_handlers
from honolulo_common.openmeteo import DEFAULT_BASE_URL, OpenMeteoClient, ProviderError
from honolulo_common.security import auth_required, internal_required
from honolulo_common.timeutil import iso_local, parse_date, parse_month

TZ = "America/Lima"


# ------------------------------------------------------------- condiciones
@pytest.mark.parametrize("wmo,expected", [
    (0, "clear"), (1, "clear"), (2, "partly_cloudy"), (3, "cloudy"), (45, "fog"),
    (53, "drizzle"), (61, "light_rain"), (63, "rain"), (65, "heavy_rain"),
    (82, "heavy_rain"), (95, "thunderstorm"), (None, "cloudy"), (77, "cloudy"),
])
def test_wmo_mapping(wmo, expected):
    assert condition_code_from_wmo(wmo) == expected


def test_night_variant_only_for_clear_and_partly_cloudy():
    assert describe("clear", is_day=False).text == "Despejado"
    assert describe("clear", is_day=True).text == "Soleado"
    assert describe("rain", is_day=False).icon == "rainy"


# ------------------------------------------------------------------ tiempo
def test_parse_month_and_date():
    assert parse_month("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert parse_month("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    with pytest.raises(ApiError):
        parse_month("2026-13")
    with pytest.raises(ApiError):
        parse_date("01/10/2026")


def test_iso_local_uses_lima_offset():
    dt = datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc)
    assert iso_local(dt, TZ) == "2026-09-30T14:00:00-05:00"


# --------------------------------------------------------------- proveedor
CURRENT_PAYLOAD = {
    "current": {"time": "2026-09-30T13:15", "temperature_2m": 26.4, "apparent_temperature": 27.1,
                "relative_humidity_2m": 78, "precipitation": 0.4, "weather_code": 2,
                "cloud_cover": 64, "wind_speed_10m": 6.2, "is_day": 1},
    "hourly": {"time": ["2026-09-30T12:00", "2026-09-30T13:00", "2026-09-30T14:00"],
               "precipitation_probability": [20, 35, 50]},
}


@responses.activate
def test_fetch_current_converts_local_time_to_utc_and_picks_hourly_probability():
    responses.add(responses.GET, DEFAULT_BASE_URL, json=CURRENT_PAYLOAD)
    reading = OpenMeteoClient(retries=0).fetch_current(-9.29, -75.99, TZ)
    assert reading.time_utc == datetime(2026, 9, 30, 18, 15, tzinfo=timezone.utc)  # 13:15 Lima = 18:15 UTC
    assert reading.temperature_c == 26.4 and reading.feels_like_c == 27.1
    assert reading.rain_probability_pct == 35
    assert reading.is_day is True


@responses.activate
def test_fetch_hourly_skips_null_hours_and_keeps_optional_nulls():
    payload = {"hourly": {
        "time": ["2026-10-01T08:00", "2026-10-01T09:00"],
        "temperature_2m": [22.5, None], "relative_humidity_2m": [82, None],
        "apparent_temperature": [23.0, None], "precipitation_probability": [None, None],
        "precipitation": [0.2, None], "weather_code": [3, None], "cloud_cover": [90, None],
        "wind_speed_10m": [4.0, None], "is_day": [1, 1]}}
    responses.add(responses.GET, DEFAULT_BASE_URL, json=payload)
    points = OpenMeteoClient(retries=0).fetch_hourly(-9.29, -75.99, TZ, forecast_days=1)
    assert len(points) == 1
    assert points[0].rain_probability_pct is None
    assert points[0].time_utc == datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)


@responses.activate
def test_provider_5xx_retries_once_then_fails():
    responses.add(responses.GET, DEFAULT_BASE_URL, status=503)
    responses.add(responses.GET, DEFAULT_BASE_URL, status=503)
    client = OpenMeteoClient(retries=1)
    with pytest.raises(ProviderError):
        client.fetch_current(-9.29, -75.99, TZ)
    assert len(responses.calls) == 2


@responses.activate
def test_provider_4xx_does_not_retry():
    responses.add(responses.GET, DEFAULT_BASE_URL, status=400, json={"error": True})
    with pytest.raises(ProviderError):
        OpenMeteoClient(retries=2).fetch_hourly(-9.29, -75.99, TZ)
    assert len(responses.calls) == 1


@responses.activate
def test_provider_invalid_payload_raises():
    responses.add(responses.GET, DEFAULT_BASE_URL, json={"unexpected": 1})
    with pytest.raises(ProviderError):
        OpenMeteoClient(retries=0).fetch_current(-9.29, -75.99, TZ)


# ---------------------------------------------------------------- seguridad
@pytest.fixture()
def app():
    app = Flask(__name__)
    app.config.update(JWT_SECRET_KEY="s" * 32, JWT_ISSUER="iss", JWT_AUDIENCE="aud",
                      INTERNAL_API_TOKEN="internal-token")
    register_error_handlers(app)

    @app.get("/private")
    @auth_required()
    def private():
        return jsonify(ok=True)

    @app.get("/admin")
    @auth_required(roles=["admin"])
    def admin():
        return jsonify(ok=True)

    @app.get("/internal")
    @internal_required
    def internal():
        return jsonify(ok=True)

    return app


def make_token(app, **overrides):
    now = datetime.now(timezone.utc)
    claims = {"sub": "u1", "iat": now, "exp": now + timedelta(minutes=5), "iss": "iss",
              "aud": "aud", "type": "access", "role": "user"}
    claims.update(overrides)
    return jwt.encode(claims, app.config["JWT_SECRET_KEY"], algorithm="HS256")


def test_auth_required_rejects_missing_invalid_and_expired(app):
    client = app.test_client()
    assert client.get("/private").status_code == 401
    assert client.get("/private", headers={"Authorization": "Bearer nope"}).status_code == 401
    expired = make_token(app, exp=datetime.now(timezone.utc) - timedelta(minutes=1))
    resp = client.get("/private", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401 and resp.get_json()["code"] == "TOKEN_EXPIRED"
    assert resp.mimetype == "application/problem+json"


def test_auth_required_accepts_valid_and_checks_roles(app):
    client = app.test_client()
    ok = {"Authorization": f"Bearer {make_token(app)}"}
    assert client.get("/private", headers=ok).status_code == 200
    assert client.get("/admin", headers=ok).status_code == 403
    admin = {"Authorization": f"Bearer {make_token(app, role='admin')}"}
    assert client.get("/admin", headers=admin).status_code == 200


def test_refresh_token_type_is_not_accepted_as_access(app):
    client = app.test_client()
    token = make_token(app, type="refresh")
    assert client.get("/private", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_internal_token(app):
    client = app.test_client()
    assert client.get("/internal").status_code == 403
    assert client.get("/internal", headers={"X-Internal-Token": "bad"}).status_code == 403
    assert client.get("/internal", headers={"X-Internal-Token": "internal-token"}).status_code == 200
