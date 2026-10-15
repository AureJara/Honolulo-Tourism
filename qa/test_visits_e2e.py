"""Afluencia de visitantes de punta a punta contra el sistema en marcha (sin mocks).

Requiere que el sistema se haya levantado con ``python scripts/dev.py up`` (en desarrollo carga visitas de
DEMOSTRACIÓN). La prueba de registro usa una cuenta administradora temporal y elimina el registro que crea."""

import datetime as dt

import pytest

from conftest import BASE

SLUG = "catarata-velo-de-las-ninfas"
STATS = "/api/v1/visits/stats"


def test_visitors_without_a_session_cannot_read_the_stats(web):
    assert web.api("GET", STATS).status_code == 401
    assert web.api("GET", "/api/v1/visits/regional").status_code == 401


def test_the_home_page_has_the_section_for_members_and_not_for_visitors(web, member_web):
    html = member_web.get("/").text
    assert 'id="afluencia"' in html and "js/visits.js" in html and 'id="visits-form"' not in html
    assert 'id="afluencia"' not in web.get("/").text


@pytest.mark.parametrize("granularity,history,forecast", [("day", 60, 14), ("month", 36, 12), ("year", None, 2)])
def test_a_member_gets_history_and_a_forecast_for_every_period(member_web, granularity, history, forecast):
    resp = member_web.api("GET", f"{STATS}?granularity={granularity}")
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    body = resp.json()
    if history:
        assert len(body["series"]) == history
    assert len(body["forecast"]) == forecast
    assert all(f["low"] <= f["expected"] <= f["high"] for f in body["forecast"])
    assert body["model"]["method"] and body["model"]["backtest_mape"] is not None
    assert body["quality"]["has_demo"] or body["quality"]["has_real"]


def test_the_daily_forecast_uses_the_weather_forecast_when_there_is_one(member_web):
    body = member_web.api("GET", f"{STATS}?granularity=day").json()
    rainy = [f for f in body["forecast"] if f["rain"] is not None]
    # sin internet el servicio del clima no tiene pronóstico guardado: entonces no hay ajuste, pero nunca un error
    assert all(0 <= f["rain"] <= 100 for f in rainy)


def test_one_place_is_a_part_of_the_total(member_web):
    total = member_web.api("GET", f"{STATS}?granularity=month").json()["kpis"]["last_30"]
    one = member_web.api("GET", f"{STATS}?granularity=month&place={SLUG}").json()
    assert one["place"]["slug"] == SLUG and 0 < one["kpis"]["last_30"] < total
    assert member_web.api("GET", f"{STATS}?place=no-existe").status_code == 404


def test_members_cannot_register_visits_but_the_admin_can_and_it_changes_the_numbers(member_web, admin_web):
    day = (dt.date.today() - dt.timedelta(days=5)).isoformat()
    payload = {"place": SLUG, "date": day, "visitors": 4321}
    assert member_web.api("POST", "/api/v1/admin/visits", json=payload).status_code == 403
    created = admin_web.api("POST", "/api/v1/admin/visits", json=payload)
    assert created.status_code == 201 and created.json()["visitors"] == 4321
    try:
        after = admin_web.api("GET", f"{STATS}?granularity=day&place={SLUG}").json()
        row = next(s for s in after["series"] if s["period"] == day)
        assert row["visitors"] == 4321 and row["source"] == "registro" and after["quality"]["has_real"]
        assert any(r["date"] == day for r in admin_web.api("GET", "/api/v1/admin/visits/recent").json()["items"])
    finally:                                   # la prueba no deja su registro (el dato de demostración de ese día se repone al reiniciar)
        removed = admin_web.api("DELETE", f"/api/v1/admin/visits?place={SLUG}&date={day}")
    assert removed.status_code == 200


def test_invalid_records_are_rejected_with_field_errors(admin_web):
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    future = admin_web.api("POST", "/api/v1/admin/visits", json={"place": SLUG, "date": tomorrow, "visitors": 5})
    assert future.status_code == 422 and future.json()["errors"][0]["field"] == "date"
    assert admin_web.api("POST", "/api/v1/admin/visits", json={"place": SLUG, "date": "2026-01-01", "visitors": -3}).status_code == 422
    assert admin_web.api("POST", "/api/v1/admin/visits", json={"place": SLUG, "date": "2026-01-01", "visitors": 99999}).status_code == 422


def test_the_regional_reference_is_the_official_series(member_web):
    body = member_web.api("GET", "/api/v1/visits/regional").json()
    assert body["series_code"] == "RD13362DM" and len(body["monthly"]) == 60 and "2019" in body["note"]
