
import pytest
import responses

from app import create_app, upstream
from honolulo_common.testing import search_or_fail

AUTH, WEATHER, FORECAST, CATALOG = "http://auth.test", "http://weather.test", "http://forecast.test", "http://catalog.test"
USER = {"id": "u1", "email": "ana@example.com", "first_name": "Ana", "last_name": "Ríos", "full_name": "Ana Ríos",
        "role": "user", "email_verified": True}
ADMIN = {**USER, "id": "a1", "email": "admin@example.com", "first_name": "Admin", "last_name": "Honolulo",
         "full_name": "Admin Honolulo", "role": "admin"}


@pytest.fixture()
def app():
    upstream.coordinator._recent.clear()
    return create_app({"TESTING": True, "SECRET_KEY": "test-web-secret-test-web-secret-123", "RATE_LIMIT_ENABLED": False, "AUTH_SERVICE_URL": AUTH, "WEATHER_SERVICE_URL": WEATHER,
                       "FORECAST_SERVICE_URL": FORECAST, "CATALOG_SERVICE_URL": CATALOG,
                       "UPSTREAM_TIMEOUT_S": 2})


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def mocked():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


def login_cookies(client, access="access-1", refresh="refresh-1"):
    client.set_cookie("hn_access", access)
    client.set_cookie("hn_refresh", refresh)


def csrf_from(client, path="/ingresar"):
    html = client.get(path).get_data(as_text=True)
    return search_or_fail(r'name="csrf_token" value="([^"]+)"', html).group(1)


def cookie_header(resp, name):
    """Devuelve la línea Set-Cookie de ``name`` o None."""
    return next((h for h in resp.headers.getlist("Set-Cookie") if h.startswith(f"{name}=")), None)


def tokens(access="access-2", refresh="refresh-2"):
    return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer", "expires_in": 900,
            "user": USER}


def set_pending_email(client, email="ana@example.com"):
    with client.session_transaction() as sess:
        sess["pending_email"] = email


def form_csrf(client, path):
    """Token CSRF de un formulario (las cookies quedan en el cliente)."""
    return csrf_from(client, path)
