"""Resiliencia (Escenario 5 y degradación): se levantan instancias alternativas de los servicios con el
proveedor caído *de verdad* (puerto cerrado) o con dependencias muertas, sin mocks."""

import os
import subprocess
import sys
import time
import uuid

import pytest
import requests

from conftest import AUTH_URL, PASSWORD, ROOT, Web, service_token

SERVICES = ROOT / "services"
DEAD = "http://127.0.0.1:9"                      # puerto cerrado: conexión rechazada
MSG_DOWN = "El servicio meteorológico no está disponible. Inténtelo nuevamente."
MSG_RN08 = "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."


def _pg_base() -> str:
    import pgserver
    srv = pgserver.get_server(str(ROOT / ".pgdata"), cleanup_mode=None)
    return srv.get_uri().replace("postgresql://", "postgresql+psycopg://", 1).rsplit("/", 1)[0]


def _new_database(prefix: str) -> str:
    import psycopg
    import pgserver
    name = f"{prefix}_{uuid.uuid4().hex[:6]}"
    uri = pgserver.get_server(str(ROOT / ".pgdata"), cleanup_mode=None).get_uri()
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    return f"{_pg_base()}/{name}"


def _base_env(**extra) -> dict:
    env = dict(os.environ, PYTHONUTF8="1", APP_ENV="development")
    env.setdefault("JWT_SECRET_KEY", "dev-secret-change-me-dev-secret-change-me")
    env.setdefault("INTERNAL_API_TOKEN", "dev-internal-token")
    env.update(extra)
    return env


class Proc:
    def __init__(self, service: str, port: int, env: dict):
        self.port = port
        self.p = subprocess.Popen([sys.executable, "-m", "flask", "--app", "wsgi", "run", "--host", "127.0.0.1",
                                   "--port", str(port), "--no-reload"], cwd=SERVICES / service, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if requests.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.3)
        self.stop()
        raise RuntimeError(f"{service} no arrancó en el puerto {port}")

    def stop(self):
        subprocess.run(["taskkill", "/PID", str(self.p.pid), "/T", "/F"], capture_output=True) if os.name == "nt" else self.p.terminate()


@pytest.fixture()
def running():
    procs = []

    def start(service, port, **env):
        proc = Proc(service, port, _base_env(**env))
        procs.append(proc)
        return proc

    yield start
    for proc in procs:
        proc.stop()


@pytest.fixture(scope="module")
def token(member):
    return service_token(member["email"], member["password"])


def headers(tok):
    return {"Authorization": f"Bearer {tok}"}


def migrate(service: str, db_url: str):
    subprocess.run([sys.executable, "-m", "flask", "--app", "app:create_app", "db", "upgrade"], cwd=SERVICES / service,
                   env=_base_env(DATABASE_URL=db_url), check=True, capture_output=True)


# ============================================ Escenario 5 · proveedor no disponible
def test_scenario5_provider_down_shows_last_stored_data_marked_as_previous(running, token):
    """Hay datos guardados pero ya vencidos y el proveedor no responde (puerto cerrado)."""
    live = requests.get("http://127.0.0.1:5002/api/v1/weather/current", headers=headers(token), timeout=20)
    assert live.status_code in (200,)                                    # asegura que existe un dato guardado
    db = f"{_pg_base()}/weather_db"
    alt = running("weather", 5012, DATABASE_URL=db, WEATHER_PROVIDER_URL=DEAD, CURRENT_MAX_AGE_MIN="0",
                  ENABLE_SCHEDULER="0", WEATHER_PROVIDER_TIMEOUT_S="2")
    resp = requests.get(f"http://127.0.0.1:{alt.port}/api/v1/weather/current", headers=headers(token), timeout=20)
    body = resp.json()
    assert resp.status_code == 200 and body["stale"] is True
    assert body["message"] == MSG_RN08
    assert body["weather"]["temperature"] is not None and body["age_seconds"] >= 0 and body["updated_at"].endswith("-05:00")


def test_scenario5_no_data_and_provider_down_reports_service_unavailable(running, token):
    db = _new_database("weather_qa_empty")
    migrate("weather", db)
    alt = running("weather", 5012, DATABASE_URL=db, WEATHER_PROVIDER_URL=DEAD, ENABLE_SCHEDULER="0",
                  WEATHER_PROVIDER_TIMEOUT_S="2")
    resp = requests.get(f"http://127.0.0.1:{alt.port}/api/v1/weather/current", headers=headers(token), timeout=20)
    body = resp.json()
    assert resp.status_code == 503 and body["code"] == "WEATHER_UNAVAILABLE" and body["detail"] == MSG_DOWN
    assert "weather" not in body                                         # nunca se presenta nada como actual


def test_scenario5_manual_refresh_with_provider_down_is_502_with_last_known(running, token):
    db = f"{_pg_base()}/weather_db"
    alt = running("weather", 5012, DATABASE_URL=db, WEATHER_PROVIDER_URL=DEAD, CURRENT_MAX_AGE_MIN="0",
                  REFRESH_MIN_INTERVAL_S="0", ENABLE_SCHEDULER="0", WEATHER_PROVIDER_TIMEOUT_S="2")
    resp = requests.post(f"http://127.0.0.1:{alt.port}/api/v1/weather/refresh", headers=headers(token), timeout=20)
    body = resp.json()
    assert resp.status_code == 502 and body["detail"] == MSG_RN08
    assert body["last_known"]["stale"] is True and "weather" in body["last_known"]


def test_forecast_keeps_serving_stored_data_when_the_provider_is_down(running, token):
    db = f"{_pg_base()}/forecast_db"
    alt = running("forecast", 5013, DATABASE_URL=db, WEATHER_PROVIDER_URL=DEAD, REFRESH_MIN_INTERVAL_S="0",
                  ENABLE_SCHEDULER="0", WEATHER_PROVIDER_TIMEOUT_S="2")
    base = f"http://127.0.0.1:{alt.port}/api/v1/weather"
    refresh = requests.post(f"{base}/forecast/refresh", headers=headers(token), timeout=20)
    assert refresh.status_code == 502 and refresh.json()["detail"] == MSG_RN08 and refresh.json()["last_issued_at"]
    month = time.strftime("%Y-%m", time.gmtime(time.time() - 5 * 3600))
    cal = requests.get(f"{base}/calendar?month={month}", headers=headers(token), timeout=20).json()
    assert any(d["available"] for d in cal["days"])                    # sigue mostrando lo guardado


# ============================================ degradación en la interfaz web
def test_web_reports_unavailable_weather_service_and_still_renders_the_page(running, member):
    alt = running("web", 8001, AUTH_SERVICE_URL=AUTH_URL, WEATHER_SERVICE_URL=DEAD, FORECAST_SERVICE_URL=DEAD,
                  CATALOG_SERVICE_URL="http://127.0.0.1:5004", UPSTREAM_TIMEOUT_S="2")
    base = f"http://127.0.0.1:{alt.port}"
    s = requests.Session()
    page = s.get(f"{base}/ingresar")
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    login = s.post(f"{base}/ingresar", data={"email": member["email"], "password": PASSWORD, "csrf_token": token}, allow_redirects=False)
    assert login.status_code == 302
    assert s.get(f"{base}/").status_code == 200                          # la página principal carga igualmente
    api = s.get(f"{base}/api/v1/weather/current")
    assert api.status_code == 503 and api.json()["code"] == "SERVICE_UNAVAILABLE" and api.json()["detail"] == MSG_DOWN
    refresh = s.post(f"{base}/api/v1/weather/refresh", headers={"X-Requested-With": "XMLHttpRequest"})
    assert refresh.status_code == 503 and refresh.json()["detail"] == MSG_DOWN
    assert s.get(f"{base}/api/v1/places").status_code == 200             # otras partes siguen funcionando


def test_web_places_degrade_gracefully_when_the_catalog_is_down(running, member):
    alt = running("web", 8001, AUTH_SERVICE_URL=AUTH_URL, CATALOG_SERVICE_URL=DEAD, UPSTREAM_TIMEOUT_S="2")
    resp = requests.get(f"http://127.0.0.1:{alt.port}/api/v1/places", timeout=15)
    assert resp.status_code == 503 and "catálogo de lugares no está disponible" in resp.json()["detail"]
    media = requests.get(f"http://127.0.0.1:{alt.port}/media/{'a' * 32}-640.webp", timeout=15)
    assert media.status_code == 503


def test_web_shows_friendly_messages_when_auth_is_down(running):
    alt = running("web", 8001, AUTH_SERVICE_URL=DEAD, UPSTREAM_TIMEOUT_S="2")
    base = f"http://127.0.0.1:{alt.port}"
    s = requests.Session()
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', s.get(f"{base}/ingresar").text).group(1)
    resp = s.post(f"{base}/ingresar", data={"email": "a@b.co", "password": "x", "csrf_token": token})
    assert resp.status_code == 503 and "servicio de autenticación no está disponible" in resp.text
    reg = re.search(r'name="csrf_token" value="([^"]+)"', s.get(f"{base}/registro").text).group(1)
    resp = s.post(f"{base}/registro", data={"first_name": "Ana", "last_name": "Ríos", "email": "a@b.co", "password": "clave1234",
                                            "password_confirm": "clave1234", "accept_privacy": "on", "csrf_token": reg})
    assert resp.status_code == 503 and "no está disponible" in resp.text
    assert "Traceback" not in resp.text
