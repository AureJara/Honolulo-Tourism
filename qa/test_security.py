"""Pruebas de seguridad (caja negra) contra el sistema real, por riesgo:
autenticación y sesión · autorización · inyección y XSS · subidas · CSRF · exposición de información."""

import base64
import datetime as dt
import io
import json
import re
import struct
import time
import zlib

import jwt
import pytest
import requests

from conftest import (AUTH_URL, BASE, CATALOG_URL, PASSWORD, Web, code_from_mail, jpeg, latest_mail, unique_email)

WEATHER_URL, FORECAST_URL = "http://127.0.0.1:5002", "http://127.0.0.1:5003"
GUESSED_SECRETS = ["secret", "dev-secret", "changeme", "honolulo", "dev-secret-change-me",
                   "dev-secret-change-me-dev-secret-change-me-", "jwt-secret", "password", "admin"]


def forged(role="admin", secret="otra-clave-de-32-bytes-otra-clave-xx", **claims):
    now = dt.datetime.now(dt.timezone.utc)
    payload = {"sub": "00000000-0000-0000-0000-000000000000", "role": role, "type": "access", "iss": "honolulo-auth",
               "aud": "honolulo-api", "iat": now, "exp": now + dt.timedelta(minutes=10), "name": "Hacker"}
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


PROTECTED = [
    (WEATHER_URL, "/api/v1/weather/current"), (WEATHER_URL, "/api/v1/weather/history"),
    (FORECAST_URL, "/api/v1/weather/calendar?month=2026-10"), (FORECAST_URL, "/api/v1/weather/scoring-parameters"),
    (CATALOG_URL, "/api/v1/admin/places"),
]


# ============================================================ JWT y sesión
@pytest.mark.parametrize("base,path", PROTECTED)
def test_tokens_signed_with_another_key_are_rejected_by_every_service(base, path):
    assert requests.get(base + path, headers=bearer(forged()), timeout=10).status_code == 401
    for secret in GUESSED_SECRETS:                                   # secretos típicos por defecto
        padded = (secret * 12)[:40]
        assert requests.get(base + path, headers=bearer(forged(secret=padded)), timeout=10).status_code == 401


@pytest.mark.parametrize("base,path", PROTECTED)
def test_alg_none_and_tampered_payloads_are_rejected(base, path, member_token):
    b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    none_token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': 'x', 'role': 'admin', 'type': 'access'})}."
    assert requests.get(base + path, headers=bearer(none_token), timeout=10).status_code == 401
    head, payload, sig = member_token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["role"] = "admin"                                          # escalada: firma original + rol cambiado
    tampered = f"{head}.{b64(claims)}.{sig}"
    assert requests.get(base + path, headers=bearer(tampered), timeout=10).status_code == 401


def test_expired_and_wrong_audience_tokens_are_rejected(member_token):
    for token in (forged(exp=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)), forged(aud="otra-api")):
        assert requests.get(f"{WEATHER_URL}/api/v1/weather/current", headers=bearer(token), timeout=10).status_code == 401
    assert requests.get(f"{WEATHER_URL}/api/v1/weather/current", headers={"Authorization": "Basic abc"}, timeout=10).status_code == 401
    assert requests.get(f"{WEATHER_URL}/api/v1/weather/current", headers={"Authorization": "Bearer"}, timeout=10).status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(member):
    login = requests.post(f"{AUTH_URL}/api/v1/auth/login", json={"email": member["email"], "password": member["password"]}, timeout=10).json()
    assert requests.get(f"{AUTH_URL}/api/v1/auth/me", headers=bearer(login["refresh_token"]), timeout=10).status_code == 401


def test_logout_revokes_the_refresh_token_and_rotation_detects_reuse(member):
    login = lambda: requests.post(f"{AUTH_URL}/api/v1/auth/login", json={"email": member["email"], "password": member["password"]}, timeout=10).json()
    a = login()
    out = requests.post(f"{AUTH_URL}/api/v1/auth/logout", json={"refresh_token": a["refresh_token"]}, timeout=10)
    assert out.status_code == 204
    assert requests.post(f"{AUTH_URL}/api/v1/auth/refresh", json={"refresh_token": a["refresh_token"]}, timeout=10).status_code == 401
    b = login()
    rotated = requests.post(f"{AUTH_URL}/api/v1/auth/refresh", json={"refresh_token": b["refresh_token"]}, timeout=10).json()
    reuse = requests.post(f"{AUTH_URL}/api/v1/auth/refresh", json={"refresh_token": b["refresh_token"]}, timeout=10)
    assert reuse.status_code == 401
    assert requests.post(f"{AUTH_URL}/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}, timeout=10).status_code == 401


def test_web_logout_ends_the_browser_session(member):
    w = Web()
    assert w.login(member["email"]).status_code == 302
    assert w.get("/").status_code == 200
    out = w.form("/salir", {}, csrf_path="/")
    assert out.status_code == 302
    assert w.get("/").status_code == 302                              # ya no hay sesión
    assert w.api("GET", "/api/v1/weather/current").status_code == 401


def test_account_lockout_after_repeated_bad_passwords():
    w, email = Web(), unique_email()
    w.register(email)
    w.verify(email)
    fresh = Web()
    for _ in range(5):
        assert fresh.login(email, "Incorrecta123").status_code == 401
    locked = fresh.login(email, PASSWORD)                             # ni siquiera la correcta entra
    assert locked.status_code == 429 and "Retry-After" in locked.headers or "intentos" in locked.text


# ======================================================== autorización / roles
def test_members_cannot_reach_admin_functions_anywhere(member_web, member_token):
    assert member_web.get("/admin/lugares").status_code == 403
    assert member_web.get("/admin/lugares/nuevo").status_code == 403
    for method, path in (("get", "/api/v1/admin/places"), ("post", "/api/v1/admin/places")):
        resp = requests.request(method, CATALOG_URL + path, headers=bearer(member_token), json={}, timeout=10)
        assert resp.status_code == 403, path
    scoring = requests.put(f"{FORECAST_URL}/api/v1/weather/scoring-parameters", headers=bearer(member_token), json={}, timeout=10)
    assert scoring.status_code == 403
    assert member_web.api("PUT", "/api/v1/weather/scoring-parameters", json={}).status_code in (403, 422)


def test_mass_assignment_cannot_grant_the_admin_role():
    email = unique_email()
    resp = requests.post(f"{AUTH_URL}/api/v1/auth/register", timeout=10, json={
        "first_name": "Carla", "last_name": "Mendoza", "email": email, "password": PASSWORD, "accept_privacy": True,
        "role": "admin", "is_active": True, "email_verified": True, "email_verified_at": "2020-01-01T00:00:00Z"})
    assert resp.status_code == 201
    # sigue sin poder entrar hasta confirmar, y al confirmar es un usuario normal
    assert requests.post(f"{AUTH_URL}/api/v1/auth/login", json={"email": email, "password": PASSWORD}, timeout=10).status_code == 403
    code = code_from_mail(latest_mail(email))
    done = requests.post(f"{AUTH_URL}/api/v1/auth/verify-email", json={"email": email, "code": code}, timeout=10).json()
    assert done["user"]["role"] == "user"


def test_internal_and_admin_apis_are_not_exposed_through_the_public_gateway(member_web):
    for path in ("/internal/observations", "/api/v1/admin/places", "/api/v1/auth/login", "/api/v1/auth/register",
                 "/api/v1/auth/refresh", "/api/v1/weather/internal/observations"):
        resp = member_web.api("GET", path)
        assert resp.status_code in (404, 405), (path, resp.status_code)
    assert requests.get(f"{WEATHER_URL}/internal/observations?from=2026-09-30T00:00:00Z&to=2026-09-30T05:00:00Z", timeout=10).status_code == 403


# ===================================================================== CSRF
def test_csrf_protection_on_forms_and_api(member):
    w = Web()
    w.login(member["email"])
    assert w.s.post(BASE + "/salir", timeout=10, allow_redirects=False).status_code == 400          # sin token
    other = Web().csrf("/ingresar")                                   # token de otra sesión
    assert w.s.post(BASE + "/salir", data={"csrf_token": other}, timeout=10, allow_redirects=False).status_code == 400
    assert w.get("/").status_code == 200                              # y la sesión sigue viva
    bare = w.s.post(BASE + "/api/v1/weather/refresh", timeout=10)    # sin la cabecera X-Requested-With
    assert bare.status_code == 403
    ok = w.api("POST", "/api/v1/weather/refresh")
    # Si el proveedor real falla en ese instante (ocurre), el contrato es el de RN08, no un error de servidor.
    assert ok.status_code == 200 or (ok.status_code == 502 and ok.json()["detail"].startswith("No se pudo actualizar"))


def test_login_form_requires_csrf_token():
    resp = requests.post(BASE + "/ingresar", data={"email": "a@b.co", "password": "x"}, timeout=10, allow_redirects=False)
    assert resp.status_code == 400


def test_register_and_verify_forms_require_csrf():
    assert requests.post(BASE + "/registro", data={"first_name": "Ana"}, timeout=10, allow_redirects=False).status_code == 400
    assert requests.post(BASE + "/verificar", data={"code": "123456"}, timeout=10, allow_redirects=False).status_code in (302, 400)


# ======================================================= redirecciones / cabeceras
@pytest.mark.parametrize("target", ["//evil.com", "https://evil.com/x", "/\\evil.com", "javascript:alert(1)", "///evil.com"])
def test_login_next_parameter_cannot_redirect_off_site(member, target):
    w = Web()
    resp = w.login(member["email"], nxt=target)
    assert resp.status_code == 302 and resp.headers["Location"] == "/"


def test_security_headers_and_cookie_flags(member):
    w = Web()
    page = w.get("/ingresar")
    assert page.headers["X-Content-Type-Options"] == "nosniff" and page.headers["X-Frame-Options"] == "DENY"
    assert "strict-origin" in page.headers["Referrer-Policy"] and "no-store" in page.headers["Cache-Control"]
    resp = w.login(member["email"])
    cookies = resp.headers.get("Set-Cookie", "")
    for name in ("hn_access", "hn_refresh"):
        line = next(c for c in re.split(r", (?=hn_)", cookies) if c.startswith(name + "="))
        assert "HttpOnly" in line and "SameSite=Lax" in line and "Path=/" in line, line
    assert "no-store" in w.get("/").headers["Cache-Control"]          # páginas con sesión no se cachean
    assert "Access-Control-Allow-Origin" not in w.api("GET", "/api/v1/weather/current").headers     # sin CORS abierto


def test_tokens_are_never_exposed_to_javascript_or_html(member):
    w = Web()
    w.login(member["email"])
    html = w.get("/").text
    access = w.s.cookies.get("hn_access")
    assert access and access not in html and "access_token" not in html and "refresh_token" not in html
    js = requests.get(BASE + "/static/js/app.js", timeout=10).text
    assert "localStorage" not in js and "sessionStorage" not in js and "document.cookie" not in js


# ========================================================== inyección y XSS
SQLI = ["' OR '1'='1", "'; DROP TABLE users;--", "1 UNION SELECT NULL--", "\" OR \"\"=\"", "%27%20OR%201=1--", "\\'", "' OR pg_sleep(3)--"]


@pytest.mark.parametrize("payload", SQLI)
def test_sql_injection_payloads_never_cause_errors_or_delays(member_web, payload):
    start = time.time()
    responses_ = [
        Web().login(payload, payload), Web().register(payload, first=payload, last=payload),
        member_web.api("GET", "/api/v1/weather/forecast", params={"date": payload}),
        member_web.api("GET", "/api/v1/weather/calendar", params={"month": payload}),
        member_web.api("GET", "/api/v1/weather/history", params={"from": payload, "page": payload}),
        member_web.api("GET", f"/api/v1/weather/evaluation/{payload}"),
        requests.get(f"{BASE}/api/v1/places/{payload}", timeout=10),
    ]
    assert time.time() - start < 9                                    # un pg_sleep(3) inyectado retrasaría
    for resp in responses_:
        assert resp.status_code < 500, (payload, resp.request.url, resp.status_code)
    # la base sigue intacta
    assert requests.get(f"{BASE}/api/v1/places", timeout=10).json()["total"] >= 4


def _purge_place(place_id: str) -> None:
    """Borra el lugar de prueba (y sus fotos en disco) para no ensuciar el entorno de desarrollo."""
    import psycopg
    import pgserver
    from conftest import ROOT
    uri = pgserver.get_server(str(ROOT / ".pgdata"), cleanup_mode=None).get_uri().rsplit("/", 1)[0]
    with psycopg.connect(uri + "/catalog_db", autocommit=True) as conn:
        keys = [r[0] for r in conn.execute("select replace(id::text, '-', '') from place_photos where place_id = %s", (place_id,))]
        conn.execute("delete from places where id = %s", (place_id,))
    media = ROOT / ".media"
    for key in keys:
        for size in ("1600", "640"):
            (media / f"{key}-{size}.webp").unlink(missing_ok=True)


XSS = ['<script>window.__xss=1</script>', '"><img src=x onerror=window.__xss=1>', "'-alert(1)-'", "<svg/onload=alert(1)>"]


def test_stored_xss_in_place_fields_is_escaped_in_every_html_view(admin_web, admin_token):
    headers = bearer(admin_token)
    created = requests.post(f"{CATALOG_URL}/api/v1/admin/places", headers=headers, timeout=10, json={
        "name": "Lugar XSS " + XSS[1], "description": "Descripción con carga: " + XSS[0] + " y " + XSS[3], "status": "draft"})
    assert created.status_code == 201
    place = created.json()
    try:
        requests.post(f"{CATALOG_URL}/api/v1/admin/places/{place['id']}/photos", headers=headers, timeout=15,
                      data={"alt_text": XSS[1]}, files={"file": ("x.jpg", jpeg(), "image/jpeg")})
        for path in ("/admin/lugares", f"/admin/lugares/{place['id']}"):
            html = admin_web.get(path).text
            assert "<script>window.__xss" not in html and "<img src=x onerror" not in html and "<svg/onload" not in html, path
            assert "&lt;script&gt;" in html or "&lt;img" in html or "&#34;&gt;&lt;img" in html, path
    finally:
        requests.patch(f"{CATALOG_URL}/api/v1/admin/places/{place['id']}", headers=headers, json={"status": "archived"}, timeout=10)
        _purge_place(place["id"])                                   # la API no borra lugares: se limpia en la base


def test_responses_use_json_content_type_so_payloads_cannot_execute_as_html(member_web):
    for path in ("/api/v1/places", "/api/v1/weather/current"):
        resp = member_web.api("GET", path)
        assert resp.headers["Content-Type"].startswith("application/json"), path


# ============================================================== subidas
def _png_bomb(w, h):
    chunk = lambda k, d: struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xFFFFFFFF)
    raw = zlib.compress((b"\x00" + b"\x00" * (w * 3)) * h, 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


@pytest.mark.parametrize("name,data", [
    pytest.param("php", b"<?php system($_GET['c']); ?>", id="php"),
    pytest.param("html", b"<html><script>alert(1)</script></html>", id="html"),
    pytest.param("svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', id="svg"),
    pytest.param("exe", b"MZ" + b"\x00" * 300, id="exe"),
    pytest.param("vacio", b"", id="vacio"),
    pytest.param("bomba", _png_bomb(9000, 9000), id="bomba-de-pixeles"),
])
def test_malicious_uploads_are_rejected_through_the_admin_ui(admin_web, admin_token, name, data):
    headers = bearer(admin_token)
    place = requests.get(f"{CATALOG_URL}/api/v1/admin/places", headers=headers, timeout=10).json()["items"][0]
    before = len(place["photos"])
    admin_web.form(f"/admin/lugares/{place['id']}/fotos", {"alt_text": "Intento de subida"}, csrf_path=f"/admin/lugares/{place['id']}",
                   files={"file": (f"{name}.jpg", data, "image/jpeg")})
    after = requests.get(f"{CATALOG_URL}/api/v1/admin/places/{place['id']}", headers=headers, timeout=10).json()
    assert len(after["photos"]) == before, name


def test_path_traversal_and_weird_keys_on_media_and_static(member_web):
    for path in ("/media/..%2f..%2f..%2fwindows%2fwin.ini", "/media/....//....//x", "/media/%2e%2e/%2e%2e/secret",
                 "/static/..%2f..%2fapp/__init__.py", "/static/../__init__.py", "/static/%2e%2e%2fconfig.py"):
        resp = requests.get(BASE + path, timeout=10)
        assert resp.status_code in (404, 400), path
        assert "SECRET_KEY" not in resp.text and "def create_app" not in resp.text, path


# ============================================== enumeración y exposición de datos
def test_login_and_resend_do_not_reveal_which_emails_exist(member):
    a = Web().login(member["email"], "Incorrecta123")
    b = Web().login("no.existe.%s@example.com" % int(time.time()), "Incorrecta123")
    assert a.status_code == b.status_code == 401
    strip = lambda t: re.sub(r'value="[^"]*"|csrf[^"]*"[^"]*"', "", t)
    assert "Correo o contraseña incorrectos." in a.text and "Correo o contraseña incorrectos." in b.text
    known = requests.post(f"{AUTH_URL}/api/v1/auth/resend-code", json={"email": member["email"]}, timeout=10)
    unknown = requests.post(f"{AUTH_URL}/api/v1/auth/resend-code", json={"email": unique_email()}, timeout=10)
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()


def test_errors_do_not_leak_stack_traces_or_internals(member_web):
    probes = [
        requests.get(BASE + "/no-existe-" + "x" * 500, timeout=10), requests.get(BASE + "/%00", timeout=10),
        requests.post(BASE + "/api/v1/weather/refresh", data="{no-json", timeout=10, headers={"X-Requested-With": "XMLHttpRequest"}),
        requests.post(AUTH_URL + "/api/v1/auth/login", data="{no-json", headers={"Content-Type": "application/json"}, timeout=10),
        requests.post(AUTH_URL + "/api/v1/auth/login", json={"email": ["a"], "password": {"x": 1}}, timeout=10),
        requests.get(BASE + "/?" + "a=" * 4000, timeout=10),
        member_web.api("GET", "/api/v1/weather/forecast?date=2026-02-30"),
        member_web.api("GET", "/api/v1/weather/evaluation/9" + "9" * 40),
    ]
    for resp in probes:
        assert resp.status_code < 500, (resp.request.url[:60], resp.status_code)
        for leak in ("Traceback", "File \"", "sqlalchemy", "psycopg", "werkzeug.exceptions", "SECRET", "JWT_SECRET"):
            assert leak not in resp.text, (resp.request.url[:60], leak)


def test_oversized_bodies_are_rejected_quickly():
    big = "x" * (200 * 1024)
    assert requests.post(BASE + "/ingresar", data={"email": big}, timeout=15, allow_redirects=False).status_code == 413
    assert requests.post(AUTH_URL + "/api/v1/auth/login", data=big, headers={"Content-Type": "application/json"}, timeout=15).status_code == 413


def test_unexpected_http_methods_are_handled(member_web):
    for method in ("TRACE", "DELETE", "PATCH", "PUT"):
        resp = requests.request(method, BASE + "/api/v1/weather/current", timeout=10)
        assert resp.status_code in (401, 403, 404, 405), method
    assert requests.options(BASE + "/api/v1/weather/current", timeout=10).status_code in (200, 204, 401, 403, 404, 405)
    assert requests.request("TRACE", BASE + "/", timeout=10).status_code in (404, 405)


def test_password_policy_is_enforced_on_the_public_registration():
    for weak in ("admin12", "12345678", "soloLetrasAqui", "corta1"):
        resp = Web().register(unique_email(), password=weak)
        assert resp.status_code == 422, weak
