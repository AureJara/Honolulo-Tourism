import json
import threading

import pytest
import requests

from conftest import AUTH, FORECAST, WEATHER, cookie_header, login_cookies, tokens

XHR = {"X-Requested-With": "XMLHttpRequest"}
CURRENT = {"location": "Honolulo", "updated_at": "2026-09-30T14:00:00-05:00",
           "weather": {"temperature": 26.4, "condition": "Parcialmente nublado"}, "stale": False}


def test_api_without_session_is_401(client):
    resp = client.get("/api/v1/weather/current")
    assert resp.status_code == 401 and resp.get_json()["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("path,base", [
    ("current", WEATHER), ("history?source=current", WEATHER), ("status", WEATHER), ("location", WEATHER),
    ("forecast?date=2026-10-01", FORECAST), ("calendar?month=2026-10", FORECAST),
    ("evaluation/152", FORECAST), ("evaluation?date=2026-10-01", FORECAST), ("scoring-parameters", FORECAST),
])
def test_routes_each_path_to_its_microservice_with_bearer_token(client, mocked, path, base):
    url = f"{base}/api/v1/weather/{path.split('?')[0]}"
    mocked.get(url, json={"ok": True}, headers={"Retry-After": "5"})
    login_cookies(client)
    resp = client.get(f"/api/v1/weather/{path}")
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    sent = mocked.calls[0].request
    assert sent.headers["Authorization"] == "Bearer access-1"
    assert sent.url.split("?", 1)[-1] == (path.split("?", 1)[1] if "?" in path else sent.url.split("?", 1)[-1])
    assert resp.headers["Retry-After"] == "5" and resp.headers["Cache-Control"] == "no-store"


def test_error_responses_are_relayed_unchanged(client, mocked):
    body = {"code": "WEATHER_UNAVAILABLE", "detail": "El servicio meteorológico no está disponible. Inténtelo nuevamente."}
    mocked.get(f"{WEATHER}/api/v1/weather/current", status=503, json=body,
               content_type="application/problem+json")
    login_cookies(client)
    resp = client.get("/api/v1/weather/current")
    assert resp.status_code == 503 and resp.get_json() == body
    assert resp.mimetype == "application/problem+json"


def test_unknown_and_disallowed_routes(client):
    login_cookies(client)
    assert client.get("/api/v1/weather/otra").status_code == 404
    assert client.post("/api/v1/weather/current", headers=XHR).status_code == 405
    assert client.post("/api/v1/weather/forecast?date=2026-10-01", headers=XHR).status_code == 405
    assert client.put("/api/v1/weather/calendar", headers=XHR).status_code == 405


def test_mutations_require_the_xhr_header(client, mocked):
    login_cookies(client)
    assert client.post("/api/v1/weather/refresh").status_code == 403
    assert client.put("/api/v1/weather/scoring-parameters", json={}).status_code == 403
    assert len(mocked.calls) == 0


def test_put_scoring_parameters_forwards_json(client, mocked):
    mocked.put(f"{FORECAST}/api/v1/weather/scoring-parameters", status=201, json={"version": 2})
    login_cookies(client)
    resp = client.put("/api/v1/weather/scoring-parameters", json={"weights": {"temperature": 1}}, headers=XHR)
    assert resp.status_code == 201
    assert json.loads(mocked.calls[0].request.body) == {"weights": {"temperature": 1}}


def test_service_down_returns_503_with_spec_message(client, mocked):
    """Escenario 5: si el servicio no responde se informa, sin inventar datos."""
    mocked.get(f"{WEATHER}/api/v1/weather/current", body=requests.ConnectionError("boom"))
    login_cookies(client)
    resp = client.get("/api/v1/weather/current")
    body = resp.get_json()
    assert resp.status_code == 503 and body["code"] == "SERVICE_UNAVAILABLE"
    assert body["detail"] == "El servicio meteorológico no está disponible. Inténtelo nuevamente."


def test_expired_access_token_is_renewed_and_request_retried(client, mocked):
    def weather(request):
        if request.headers.get("Authorization") == "Bearer access-1":
            return 401, {}, json.dumps({"code": "TOKEN_EXPIRED"})
        return 200, {"Content-Type": "application/json"}, json.dumps(CURRENT)

    mocked.add_callback(mocked.GET, f"{WEATHER}/api/v1/weather/current", callback=weather)
    mocked.post(f"{AUTH}/api/v1/auth/refresh", json=tokens("access-2", "refresh-2"))
    login_cookies(client)
    resp = client.get("/api/v1/weather/current")
    assert resp.status_code == 200 and resp.get_json()["location"] == "Honolulo"
    assert "hn_access=access-2" in cookie_header(resp, "hn_access")
    assert "hn_refresh=refresh-2" in cookie_header(resp, "hn_refresh")


def test_only_refresh_cookie_present_renews_before_calling(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/refresh", json=tokens("fresh", "fresh-ref"))
    mocked.get(f"{WEATHER}/api/v1/weather/current", json=CURRENT)
    client.set_cookie("hn_refresh", "refresh-1")
    resp = client.get("/api/v1/weather/current")
    assert resp.status_code == 200
    assert mocked.calls[-1].request.headers["Authorization"] == "Bearer fresh"


def test_dead_session_returns_401_and_clears_cookies(client, mocked):
    mocked.get(f"{WEATHER}/api/v1/weather/current", status=401, json={"code": "TOKEN_EXPIRED"})
    mocked.post(f"{AUTH}/api/v1/auth/refresh", status=401, json={"code": "INVALID_REFRESH_TOKEN"})
    login_cookies(client)
    resp = client.get("/api/v1/weather/current")
    assert resp.status_code == 401
    assert "hn_access=;" in cookie_header(resp, "hn_access")


def test_me_endpoint(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", json={"email": "ana@example.com"})
    login_cookies(client)
    assert client.get("/api/v1/auth/me").get_json()["email"] == "ana@example.com"
    client.delete_cookie("hn_access")
    client.delete_cookie("hn_refresh")
    assert client.get("/api/v1/auth/me").status_code == 401


# ------------------------------------------------ refresco combinado (RF07)
def _refresh_mocks(mocked, weather=(200, {"refreshed": True, "weather": {}, "stale": False}),
                   forecast=(200, {"refreshed": True})):
    mocked.post(f"{WEATHER}/api/v1/weather/refresh", status=weather[0], json=weather[1])
    mocked.post(f"{FORECAST}/api/v1/weather/forecast/refresh", status=forecast[0], json=forecast[1])


def test_combined_refresh_updates_both_services(client, mocked):
    _refresh_mocks(mocked)
    login_cookies(client)
    body = client.post("/api/v1/weather/refresh", headers=XHR).get_json()
    assert body["refreshed"] is True and body["current"]["stale"] is False
    assert body["forecast"] == {"refreshed": True} and "message" not in body


def test_combined_refresh_with_debounced_services_reports_not_refreshed(client, mocked):
    _refresh_mocks(mocked, weather=(200, {"refreshed": False, "reason": "recent", "weather": {}}),
                   forecast=(200, {"refreshed": False, "reason": "recent"}))
    login_cookies(client)
    body = client.post("/api/v1/weather/refresh", headers=XHR).get_json()
    assert body["refreshed"] is False


def test_combined_refresh_partial_failure_returns_last_known_with_message(client, mocked):
    last = {"stale": True, "weather": {"temperature": 25}, "message": "x"}
    _refresh_mocks(mocked, weather=(502, {"code": "WEATHER_PROVIDER_UNAVAILABLE", "last_known": last}))
    login_cookies(client)
    resp = client.post("/api/v1/weather/refresh", headers=XHR)
    body = resp.get_json()
    assert resp.status_code == 200 and body["current"] == last
    assert body["message"] == "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."


def test_combined_refresh_total_failure_is_502_with_rn08_message(client, mocked):
    last = {"stale": True, "weather": {"temperature": 25}}
    _refresh_mocks(mocked, weather=(502, {"last_known": last}), forecast=(502, {"code": "X"}))
    login_cookies(client)
    resp = client.post("/api/v1/weather/refresh", headers=XHR)
    assert resp.status_code == 502
    assert resp.get_json()["detail"] == "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."
    assert resp.get_json()["last_known"] == last


def test_combined_refresh_with_both_services_down_is_503(client, mocked):
    mocked.post(f"{WEATHER}/api/v1/weather/refresh", body=requests.ConnectionError("x"))
    mocked.post(f"{FORECAST}/api/v1/weather/forecast/refresh", body=requests.ConnectionError("x"))
    login_cookies(client)
    resp = client.post("/api/v1/weather/refresh", headers=XHR)
    assert resp.status_code == 503 and resp.get_json()["code"] == "SERVICE_UNAVAILABLE"


def test_parallel_calls_with_expired_token_rotate_the_refresh_token_only_once(client, mocked):
    """Reusar un refresh token ya rotado revocaría la sesión: las dos llamadas paralelas
    deben compartir una única renovación."""
    refresh_calls, lock = [], threading.Lock()

    def refresh(request):
        with lock:
            refresh_calls.append(json.loads(request.body)["refresh_token"])
        return 200, {"Content-Type": "application/json"}, json.dumps(tokens("access-2", "refresh-2"))

    def service(ok_body):
        def handler(request):
            if request.headers.get("Authorization") != "Bearer access-2":
                return 401, {}, json.dumps({"code": "TOKEN_EXPIRED"})
            return 200, {"Content-Type": "application/json"}, json.dumps(ok_body)
        return handler

    mocked.add_callback(mocked.POST, f"{AUTH}/api/v1/auth/refresh", callback=refresh)
    mocked.add_callback(mocked.POST, f"{WEATHER}/api/v1/weather/refresh",
                        callback=service({"refreshed": True, "weather": {}, "stale": False}))
    mocked.add_callback(mocked.POST, f"{FORECAST}/api/v1/weather/forecast/refresh",
                        callback=service({"refreshed": True}))
    login_cookies(client)
    resp = client.post("/api/v1/weather/refresh", headers=XHR)
    assert resp.status_code == 200 and resp.get_json()["refreshed"] is True
    assert refresh_calls == ["refresh-1"]
    assert "hn_access=access-2" in cookie_header(resp, "hn_access")
