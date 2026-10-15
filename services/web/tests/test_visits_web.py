"""Afluencia de visitantes en la web: pasarela hacia el catálogo, permisos y la sección de la página."""

import pytest
import requests

from conftest import ADMIN, AUTH, CATALOG, FORECAST, USER, login_cookies
from test_admin import as_admin

XHR = {"X-Requested-With": "XMLHttpRequest"}
STATS = {"granularity": "month", "series": [], "forecast": [], "kpis": {}, "model": {"method": None},
         "quality": {"has_demo": False}}


def as_user(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", json=USER)
    login_cookies(client)


def catalog_calls(mocked):
    return [c for c in mocked.calls if "catalog.test" in c.request.url]


def test_the_stats_require_a_session_and_do_not_reach_the_catalog_without_one(client, mocked):
    assert client.get("/api/v1/visits/stats").status_code == 401
    assert client.get("/api/v1/visits/regional").status_code == 401
    assert client.post("/api/v1/admin/visits", json={}, headers=XHR).status_code == 401
    assert catalog_calls(mocked) == []


def test_a_member_gets_the_stats_with_their_session_forwarded(client, mocked):
    as_user(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/visits/stats", json=STATS)
    resp = client.get("/api/v1/visits/stats?granularity=month&place=cascada-san-miguel&evil=1")
    assert resp.status_code == 200 and resp.get_json()["granularity"] == "month"
    request = catalog_calls(mocked)[0].request
    assert request.headers["Authorization"] == "Bearer access-1"
    assert "place=cascada-san-miguel" in request.url and "evil" not in request.url and "rain" not in request.url


def test_the_daily_view_adds_the_rain_probability_of_the_next_days(client, mocked):
    as_user(client, mocked)
    mocked.get(f"{FORECAST}/api/v1/weather/forecast", json={"available": True, "forecast": [
        {"time": "06:00", "rain_probability": 100}, {"time": "09:00", "rain_probability": 40},
        {"time": "15:00", "rain_probability": 60}, {"time": "22:00", "rain_probability": 100}]})
    mocked.get(f"{CATALOG}/api/v1/visits/stats", json=STATS)
    assert client.get("/api/v1/visits/stats?granularity=day").status_code == 200
    url = catalog_calls(mocked)[0].request.url
    assert "rain=" in url and "%3A50" in url                      # media de 09:00 y 15:00 (solo horario de visita)
    assert url.count("%3A") == 7                                  # una probabilidad por cada uno de los 7 días


def test_without_weather_the_daily_stats_still_work(client, mocked):
    as_user(client, mocked)
    mocked.get(f"{FORECAST}/api/v1/weather/forecast", body=requests.ConnectionError("x"))
    mocked.get(f"{CATALOG}/api/v1/visits/stats", json=STATS)
    assert client.get("/api/v1/visits/stats?granularity=day").status_code == 200
    assert "rain=" not in catalog_calls(mocked)[0].request.url


@pytest.mark.parametrize("bad", ["..%2fx", "A B", "UPPER", "a" * 81])
def test_the_place_is_validated_before_calling_the_catalog(client, mocked, bad):
    as_user(client, mocked)
    assert client.get(f"/api/v1/visits/stats?place={bad}").status_code == 404
    assert catalog_calls(mocked) == []


def test_registering_visits_needs_the_csrf_header_and_is_forwarded_for_the_catalog_to_judge_the_role(client, mocked):
    as_admin(client, mocked)
    mocked.post(f"{CATALOG}/api/v1/admin/visits", json={"visitors": 5}, status=201)
    body = {"place": "cascada-san-miguel", "date": "2026-10-10", "visitors": 5}
    assert client.post("/api/v1/admin/visits", json=body).status_code == 403
    assert catalog_calls(mocked) == []
    assert client.post("/api/v1/admin/visits", json=body, headers=XHR).status_code == 201
    assert catalog_calls(mocked)[0].request.headers["Authorization"] == "Bearer access-1"


def test_a_plain_user_is_refused_by_the_catalog_and_the_answer_is_relayed(client, mocked):
    as_user(client, mocked)
    mocked.post(f"{CATALOG}/api/v1/admin/visits", status=403,
                json={"type": "about:blank", "title": "Forbidden", "status": 403, "code": "FORBIDDEN",
                      "detail": "No tienes permisos para esta operación."})
    resp = client.post("/api/v1/admin/visits", json={"place": "x", "date": "2026-10-10", "visitors": 5}, headers=XHR)
    assert resp.status_code == 403


def test_clearing_the_demo_data_needs_the_csrf_header(client, mocked):
    as_admin(client, mocked)
    mocked.delete(f"{CATALOG}/api/v1/admin/visits/demo", json={"deleted": 10})
    assert client.delete("/api/v1/admin/visits/demo").status_code == 403
    assert client.delete("/api/v1/admin/visits/demo", headers=XHR).get_json() == {"deleted": 10}


def test_the_catalog_down_is_reported_as_unavailable(client, mocked):
    as_user(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/visits/stats", body=requests.ConnectionError("x"))
    resp = client.get("/api/v1/visits/stats")
    assert resp.status_code == 503 and "visitas no está disponible" in resp.get_json()["detail"]


def test_members_get_the_section_and_only_admins_get_the_registration_form(client, mocked):
    as_user(client, mocked)
    html = client.get("/").get_data(as_text=True)
    assert 'id="afluencia"' in html and "Afluencia de visitantes" in html and "js/visits.js" in html
    assert 'data-visits-gran="day"' in html and 'id="visits-chart"' in html and "#afluencia" in html
    assert 'id="visits-form"' not in html and "Registrar visitas" not in html
    admin_client = client.application.test_client()
    as_admin(admin_client, mocked, ADMIN)
    admin_html = admin_client.get("/").get_data(as_text=True)
    assert 'id="visits-form"' in admin_html and 'id="visits-clear-demo"' in admin_html


def test_removing_a_record_is_forwarded_with_the_csrf_header_and_a_valid_place(client, mocked):
    as_admin(client, mocked)
    mocked.delete(f"{CATALOG}/api/v1/admin/visits", json={"deleted": 1})
    url = "/api/v1/admin/visits?place=cascada-san-miguel&date=2026-10-10"
    assert client.delete(url).status_code == 403
    assert client.delete(url, headers=XHR).get_json() == {"deleted": 1}
    assert "place=cascada-san-miguel" in catalog_calls(mocked)[0].request.url
    assert client.delete("/api/v1/admin/visits?place=A%20B&date=2026-10-10", headers=XHR).status_code == 404
