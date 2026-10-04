"""Pantalla de actividad de seguridad para administradores (los eventos los registra el servicio de cuentas)."""

import requests
import responses

from conftest import AUTH, CATALOG, USER, login_cookies
from test_admin import as_admin

AUDIT = f"{AUTH}/api/v1/auth/admin/audit"
EVENTS = ["login_failed", "login_success", "account_locked", "role_changed"]


def item(n, event="login_failed", **extra):
    base = {"id": n, "occurred_at": "2026-10-03T22:15:03.123456+00:00", "event": event, "user_id": "0b8f2c1e-5c7a-4d0e-9d57-2a5c3b1e9f10",
            "email_hint": "a***@example.com", "email_hash": "abcdef0123456789", "ip": "203.0.113.9", "detail": {"reason": "bad_credentials"}}
    return {**base, **extra}


def body(items, total=None):
    return {"items": items, "total": len(items) if total is None else total, "events": EVENTS}


def audit_calls(mocked):
    return [c for c in mocked.calls if c.request.url.startswith(AUDIT)]


def test_requires_login_and_the_admin_role(client, mocked):
    resp = client.get("/admin/seguridad")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/ingresar?next=")
    as_admin(client, mocked, user=USER)
    assert client.get("/admin/seguridad").status_code == 403
    assert audit_calls(mocked) == []                                     # a un usuario normal nunca se le consulta nada


def test_events_are_listed_with_spanish_labels_and_severity(client, mocked):
    as_admin(client, mocked)
    mocked.get(AUDIT, json=body([item(3, "account_locked", detail={"minutes": 15}), item(2, "login_success", detail={}),
                                 item(1, "refresh_reuse", detail={"family": "abcd1234"})]))
    html = client.get("/admin/seguridad").get_data(as_text=True)
    for expected in ("Cuenta bloqueada por intentos fallidos", "Ingreso correcto", "Posible robo de sesión", "a***@example.com",
                     "203.0.113.9", "minutes: <strong>15</strong>", "2026-10-03 22:15:03", "0b8f2c1e"):
        assert expected in html, expected
    assert "bg-error-container" in html and "Actividad de seguridad" in html
    assert "abcdef0123456789" not in html                                 # la huella del correo no se muestra: no hace falta


def test_everything_from_the_log_is_escaped(client, mocked):
    as_admin(client, mocked)
    mocked.get(AUDIT, json=body([item(1, email_hint="<b>x</b>***@e.co", ip="<script>alert(1)</script>", detail={"k": "<img src=x onerror=alert(1)>"})]))
    html = client.get("/admin/seguridad").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "<img src=x" not in html and "<b>x</b>" not in html and "&lt;script&gt;" in html


def test_an_event_type_the_page_does_not_know_still_shows_with_its_code(client, mocked):
    as_admin(client, mocked)
    mocked.get(AUDIT, json=body([item(1, "evento_nuevo_del_futuro")]))
    assert "evento_nuevo_del_futuro" in client.get("/admin/seguridad").get_data(as_text=True)


def test_filter_and_pages_are_forwarded_to_the_auth_service(client, mocked):
    as_admin(client, mocked)
    mocked.get(AUDIT, json=body([item(1)], total=120))
    html = client.get("/admin/seguridad?evento=login_failed&pagina=2").get_data(as_text=True)
    query = audit_calls(mocked)[0].request.url
    assert "event=login_failed" in query and "limit=50" in query and "offset=50" in query
    assert "Página 2 de 3" in html and "120 eventos" in html
    assert "?pagina=1&amp;evento=login_failed" in html or "?pagina=1&evento=login_failed" in html      # volver conserva el filtro
    assert "?pagina=3" in html and "Más antiguos" in html
    assert 'value="login_failed" selected' in html


def test_bad_page_numbers_fall_back_to_the_first_page(client, mocked):
    as_admin(client, mocked)
    mocked.get(AUDIT, json=body([item(1)]))
    for bad in ("abc", "-4", "0", "1.5"):
        assert client.get(f"/admin/seguridad?pagina={bad}").status_code == 200
    assert all("offset=0" in c.request.url for c in audit_calls(mocked))


def test_an_unknown_event_filter_shows_a_clear_message(client, mocked):
    as_admin(client, mocked)
    mocked.get(AUDIT, status=422, json={"code": "VALIDATION_ERROR", "detail": "x", "errors": [{"field": "event", "message": "Tipo de evento desconocido."}]})
    resp = client.get("/admin/seguridad?evento=inventado")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "No hay eventos" in html and "Ese tipo de evento no existe." in html


def test_when_auth_fails_the_page_explains_it(client, mocked):
    as_admin(client, mocked)
    mocked.get(AUDIT, status=500, json={"code": "INTERNAL_ERROR", "detail": "x"})
    failed = client.get("/admin/seguridad")
    assert failed.status_code == 200 and "No se pudo cargar la actividad de seguridad." in failed.get_data(as_text=True)
    mocked.replace(responses.GET, AUDIT, body=requests.ConnectionError("caído"))
    down = client.get("/admin/seguridad")
    assert down.status_code == 503 and "servicio de cuentas no está disponible" in down.get_data(as_text=True)


def test_the_places_page_links_to_the_security_activity(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places", json={"items": [], "total": 0})
    assert 'href="/admin/seguridad"' in client.get("/admin/lugares").get_data(as_text=True)
