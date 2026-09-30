import requests

from conftest import AUTH, USER, cookie_header, csrf_from, login_cookies, tokens


def test_home_requires_session_and_redirects_to_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/ingresar?next=/"


def test_login_page_sets_csrf_cookie_and_renders_form(client):
    resp = client.get("/ingresar")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'name="email"' in html and 'name="csrf_token"' in html
    assert "hn_csrf=" in cookie_header(resp, "hn_csrf") and "HttpOnly" in cookie_header(resp, "hn_csrf")


def test_login_without_csrf_token_is_rejected(client, mocked):
    resp = client.post("/ingresar", data={"email": "a@b.co", "password": "x"})
    assert resp.status_code == 400 and len(mocked.calls) == 0


def test_login_success_sets_httponly_cookies_and_follows_next(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/login", json=tokens("acc", "ref"))
    token = csrf_from(client)
    resp = client.post("/ingresar", data={"email": "ana@example.com", "password": "clave1234",
                                          "csrf_token": token, "next": "/"})
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    access, refresh = cookie_header(resp, "hn_access"), cookie_header(resp, "hn_refresh")
    assert "hn_access=acc" in access and "HttpOnly" in access and "SameSite=Lax" in access
    assert "hn_refresh=ref" in refresh and "HttpOnly" in refresh


def test_login_blocks_open_redirects(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/login", json=tokens())
    token = csrf_from(client)
    for evil in ("//evil.com", "https://evil.com", "/\\evil.com"):
        resp = client.post("/ingresar", data={"email": "a@b.co", "password": "x", "csrf_token": token, "next": evil})
        assert resp.headers["Location"] == "/"


def test_login_failure_shows_message_and_sets_no_session(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/login", status=401,
                json={"code": "INVALID_CREDENTIALS", "detail": "Correo o contraseña incorrectos."})
    token = csrf_from(client)
    resp = client.post("/ingresar", data={"email": "a@b.co", "password": "mala", "csrf_token": token})
    assert resp.status_code == 401
    assert "Correo o contraseña incorrectos." in resp.get_data(as_text=True)
    assert cookie_header(resp, "hn_access") is None


def test_login_when_auth_service_is_down(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/login", body=requests.ConnectionError("boom"))
    token = csrf_from(client)
    resp = client.post("/ingresar", data={"email": "a@b.co", "password": "x", "csrf_token": token})
    assert resp.status_code == 503 and "no está disponible" in resp.get_data(as_text=True)


def test_home_renders_main_screen_for_active_session(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", json=USER)
    login_cookies(client)
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    for expected in ("Planifica tu Visita en las Cascadas", "Actualizar clima", "Monitoreo meteorológico",
                     "Ana Ríos", "Mariano Dámaso Beraún", "Leoncio Prado", "9°17′44″S 75°59′51″O",
                     "Historial meteorológico reciente"):
        assert expected in html
    assert mocked.calls[0].request.headers["Authorization"] == "Bearer access-1"
    assert "no-store" in resp.headers["Cache-Control"]


def test_home_renews_expired_access_token_with_refresh_token(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", status=401, json={"code": "TOKEN_EXPIRED"})
    mocked.get(f"{AUTH}/api/v1/auth/me", json=USER)
    mocked.post(f"{AUTH}/api/v1/auth/refresh", json=tokens("new-acc", "new-ref"))
    login_cookies(client)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "hn_access=new-acc" in cookie_header(resp, "hn_access")
    assert "hn_refresh=new-ref" in cookie_header(resp, "hn_refresh")
    assert mocked.calls[-1].request.headers["Authorization"] == "Bearer new-acc"


def test_home_with_dead_session_redirects_and_clears_cookies(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", status=401, json={"code": "TOKEN_EXPIRED"})
    mocked.post(f"{AUTH}/api/v1/auth/refresh", status=401, json={"code": "INVALID_REFRESH_TOKEN"})
    login_cookies(client)
    resp = client.get("/")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/ingresar")
    assert "Max-Age=0" in cookie_header(resp, "hn_access") or "Expires=" in cookie_header(resp, "hn_access")


def test_home_when_auth_service_is_down_shows_error_page(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", body=requests.ConnectionError("boom"))
    login_cookies(client)
    resp = client.get("/")
    assert resp.status_code == 503 and "autenticación no está disponible" in resp.get_data(as_text=True)


def test_logout_revokes_refresh_token_and_clears_cookies(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/logout", status=204)
    token = csrf_from(client)                     # antes de tener sesión: /ingresar no consulta a auth
    login_cookies(client, "acc", "ref-to-revoke")
    resp = client.post("/salir", data={"csrf_token": token})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/ingresar")
    assert b"ref-to-revoke" in mocked.calls[-1].request.body
    assert "hn_refresh=;" in cookie_header(resp, "hn_refresh")


def test_logout_requires_csrf_token(client, mocked):
    login_cookies(client)
    assert client.post("/salir").status_code == 400


def test_health_and_unknown_pages(client):
    assert client.get("/healthz").get_json() == {"status": "ok", "service": "web"}
    page = client.get("/no-existe")
    assert page.status_code == 404 and "text/html" in page.content_type
    api = client.get("/api/v1/otra-cosa")
    assert api.status_code == 404 and api.get_json()["status"] == 404
