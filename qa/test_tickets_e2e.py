"""Boletería de punta a punta contra el sistema en marcha (sin mocks): precio, compra, mis boletos, anulación y puerta.

Requiere ``python scripts/dev.py up``. El pago es simulado. Las pruebas anulan o validan los boletos que crean."""

import datetime as dt

from conftest import BASE

SLUG = "cascada-san-miguel"


def day(n=0):
    return (dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date() + dt.timedelta(days=n)).isoformat()


def buy(client, n=0, qty=3, method="yape"):
    return client.api("POST", "/api/v1/tickets", json={"place": SLUG, "date": day(n), "quantity": qty, "payment_method": method})


def test_the_price_is_public_and_the_home_page_shows_the_box_office(web, member_web):
    cfg = web.api("GET", "/api/v1/tickets/config")
    assert cfg.status_code == 200 and cfg.json()["price_cents"] == 500
    visitor = web.get("/").text
    assert 'id="boleteria"' in visitor and 'id="tk-form"' not in visitor and "necesitas una cuenta" in visitor
    assert 'id="tk-form"' in member_web.get("/").text


def test_visitors_cannot_buy(web):
    assert web.api("POST", "/api/v1/tickets", json={"place": SLUG, "date": day(1), "quantity": 1, "payment_method": "yape"}).status_code == 401
    assert web.api("GET", "/api/v1/tickets/mine").status_code == 401


def test_a_member_buys_sees_it_and_cancels_it(member_web):
    resp = buy(member_web, 2, 3)
    assert resp.status_code == 201
    order = resp.json()
    assert order["total_cents"] == 1500 and order["status"] == "pagado" and order["simulated_payment"] is True
    assert resp.headers["Cache-Control"] == "no-store"
    assert order["code"] in [t["code"] for t in member_web.api("GET", "/api/v1/tickets/mine").json()["items"]]
    cancelled = member_web.api("POST", "/api/v1/tickets/cancel", json={"code": order["code"]})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "anulado"


def test_invalid_purchases_are_rejected(member_web):
    assert buy(member_web, -1).status_code == 422
    assert buy(member_web, 1, 0).status_code == 422
    assert buy(member_web, 1, 21).status_code == 422
    assert buy(member_web, 1, 1, "bitcoin").status_code == 422


def test_only_the_admin_validates_at_the_door_and_only_once(member_web, admin_web):
    code = buy(member_web, 0, 2).json()["code"]                    # boleto para hoy
    assert member_web.api("POST", "/api/v1/admin/tickets/validate", json={"code": code}).status_code == 403
    ok = admin_web.api("POST", "/api/v1/admin/tickets/validate", json={"code": code})
    assert ok.status_code == 200 and ok.json()["status"] == "usado"
    assert admin_web.api("POST", "/api/v1/admin/tickets/validate", json={"code": code}).status_code == 409
    summary = admin_web.api("GET", "/api/v1/admin/tickets/summary").json()
    assert summary["sold_total"]["tickets"] >= 2 and summary["used_today"] >= 2
    assert member_web.api("GET", "/api/v1/admin/tickets/summary").status_code == 403
