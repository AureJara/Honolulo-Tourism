"""Boletería en la web: pasarela hacia el catálogo, permisos y la sección de la página."""

import requests

from conftest import ADMIN, CATALOG
from test_admin import as_admin
from test_visits_web import XHR, as_user, catalog_calls

ORDER = {"code": "HN-ABCD2345", "total_cents": 1000, "quantity": 2, "status": "pagado"}


def test_the_price_is_public(client, mocked):
    mocked.get(f"{CATALOG}/api/v1/tickets/config", json={"price_cents": 500})
    resp = client.get("/api/v1/tickets/config")
    assert resp.status_code == 200 and resp.get_json()["price_cents"] == 500
    assert "Authorization" not in catalog_calls(mocked)[0].request.headers


def test_everything_else_needs_a_session_and_never_reaches_the_catalog_without_one(client, mocked):
    assert client.post("/api/v1/tickets", json={}, headers=XHR).status_code == 401
    assert client.get("/api/v1/tickets/mine").status_code == 401
    assert client.post("/api/v1/tickets/cancel", json={}, headers=XHR).status_code == 401
    assert client.post("/api/v1/admin/tickets/validate", json={}, headers=XHR).status_code == 401
    assert client.get("/api/v1/admin/tickets/summary").status_code == 401
    assert catalog_calls(mocked) == []


def test_buying_needs_the_csrf_header_and_forwards_the_session(client, mocked):
    as_user(client, mocked)
    mocked.post(f"{CATALOG}/api/v1/tickets", json=ORDER, status=201)
    body = {"place": "cascada-san-miguel", "date": "2026-10-20", "quantity": 2, "payment_method": "yape"}
    assert client.post("/api/v1/tickets", json=body).status_code == 403
    assert catalog_calls(mocked) == []
    resp = client.post("/api/v1/tickets", json=body, headers=XHR)
    assert resp.status_code == 201 and resp.get_json()["total_cents"] == 1000
    assert catalog_calls(mocked)[0].request.headers["Authorization"] == "Bearer access-1"


def test_the_price_sent_by_the_browser_is_just_passed_on_for_the_catalog_to_ignore(client, mocked):
    as_user(client, mocked)
    mocked.post(f"{CATALOG}/api/v1/tickets", json=ORDER, status=201)
    client.post("/api/v1/tickets", json={"quantity": 2, "total_cents": 1}, headers=XHR)
    assert b"total_cents" in catalog_calls(mocked)[0].request.body            # no se interpreta aquí; decide el catálogo


def test_my_tickets_and_cancelling(client, mocked):
    as_user(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/tickets/mine", json={"items": [ORDER]})
    mocked.post(f"{CATALOG}/api/v1/tickets/cancel", json={**ORDER, "status": "anulado"})
    assert client.get("/api/v1/tickets/mine").get_json()["items"][0]["code"] == "HN-ABCD2345"
    assert client.post("/api/v1/tickets/cancel", json={"code": "HN-ABCD2345"}).status_code == 403
    assert client.post("/api/v1/tickets/cancel", json={"code": "HN-ABCD2345"}, headers=XHR).get_json()["status"] == "anulado"


def test_a_plain_user_is_refused_by_the_catalog_at_the_door_and_in_the_summary(client, mocked):
    as_user(client, mocked)
    refusal = {"type": "about:blank", "title": "Forbidden", "status": 403, "code": "FORBIDDEN",
               "detail": "No tienes permisos para esta operación."}
    mocked.post(f"{CATALOG}/api/v1/admin/tickets/validate", status=403, json=refusal)
    mocked.get(f"{CATALOG}/api/v1/admin/tickets/summary", status=403, json=refusal)
    assert client.post("/api/v1/admin/tickets/validate", json={"code": "HN-ABCD2345"}, headers=XHR).status_code == 403
    assert client.get("/api/v1/admin/tickets/summary").status_code == 403


def test_the_admin_validates_and_sees_the_sales(client, mocked):
    as_admin(client, mocked)
    mocked.post(f"{CATALOG}/api/v1/admin/tickets/validate", json={**ORDER, "status": "usado"})
    mocked.get(f"{CATALOG}/api/v1/admin/tickets/summary", json={"sold_total": {"tickets": 2}})
    assert client.post("/api/v1/admin/tickets/validate", json={"code": "HN-ABCD2345"}).status_code == 403
    assert client.post("/api/v1/admin/tickets/validate", json={"code": "HN-ABCD2345"}, headers=XHR).get_json()["status"] == "usado"
    assert client.get("/api/v1/admin/tickets/summary").get_json()["sold_total"]["tickets"] == 2


def test_the_catalog_down_is_reported_as_unavailable(client, mocked):
    as_user(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/tickets/mine", body=requests.ConnectionError("x"))
    resp = client.get("/api/v1/tickets/mine")
    assert resp.status_code == 503 and "boletería no está disponible" in resp.get_json()["detail"]


def test_a_visitor_sees_the_price_and_an_invitation_to_log_in_but_no_purchase_form(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="boleteria"' in html and "S/ 5.00" in html and "necesitas una cuenta" in html
    assert 'id="tk-form"' not in html and 'id="tk-admin"' not in html and "js/tickets.js" in html


def test_members_get_the_purchase_form_and_only_admins_get_the_door_panel(client, mocked):
    as_user(client, mocked)
    html = client.get("/").get_data(as_text=True)
    assert 'id="tk-form"' in html and 'id="tk-mine"' in html and "Pago simulado" in html and "#boleteria" in html
    assert 'id="tk-admin"' not in html and "Validar entrada" not in html
    admin_client = client.application.test_client()
    as_admin(admin_client, mocked, ADMIN)
    assert 'id="tk-admin"' in admin_client.get("/").get_data(as_text=True)
