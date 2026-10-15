"""Boletería: precio, compra (S/ 5 por persona), mis boletos, anulación y validación en la puerta."""

from datetime import timedelta

import pytest

from app import tickets, visits
from conftest import token

VELO = "catarata-velo-de-las-ninfas"
BUY = "/api/v1/tickets"


def hdr(role="user", sub="u-1", name="Ana Ríos"):
    return {"Authorization": f"Bearer {token(role=role, sub=sub, name=name)}"}


def body(days=0, qty=2, method="yape", place=VELO):
    return {"place": place, "date": (visits.today_pe() + timedelta(days=days)).isoformat(), "quantity": qty,
            "payment_method": method}


def test_the_price_is_public_and_five_soles_per_person(client):
    cfg = client.get("/api/v1/tickets/config").get_json()
    assert cfg["price_cents"] == 500 and cfg["currency"] == "PEN" and cfg["simulated_payment"] is True


def test_buying_requires_a_session(seeded):
    assert seeded.post(BUY, json=body()).status_code == 401
    assert seeded.get(f"{BUY}/mine").status_code == 401


@pytest.mark.parametrize("qty,total", [(1, 500), (2, 1000), (7, 3500), (20, 10000)])
def test_the_total_is_five_soles_times_the_people(seeded, qty, total):
    resp = seeded.post(BUY, json=body(1, qty), headers=hdr())
    assert resp.status_code == 201
    order = resp.get_json()
    assert order["total_cents"] == total and order["unit_price_cents"] == 500 and order["status"] == "pagado"
    assert order["code"].startswith("HN-") and len(order["code"]) == 11 and order["buyer_name"] == "Ana Ríos"
    assert order["simulated_payment"] is True


def test_codes_are_unique(seeded):
    codes = {seeded.post(BUY, json=body(1, 1), headers=hdr()).get_json()["code"] for _ in range(15)}
    assert len(codes) == 15


@pytest.mark.parametrize("change", [
    {"quantity": 0}, {"quantity": 21}, {"quantity": 1.5}, {"quantity": "2"}, {"payment_method": "bitcoin"},
    {"place": "NO válido"}, {"date": "ayer"}, {"payment_method": None}])
def test_invalid_purchases_are_rejected(seeded, change):
    assert seeded.post(BUY, json={**body(1), **change}, headers=hdr()).status_code == 422


def test_past_dates_and_too_far_dates_are_rejected(seeded):
    assert seeded.post(BUY, json=body(-1), headers=hdr()).status_code == 422
    assert seeded.post(BUY, json=body(91), headers=hdr()).status_code == 422
    assert seeded.post(BUY, json=body(0), headers=hdr()).status_code == 201      # hoy sí
    assert seeded.post(BUY, json=body(90), headers=hdr()).status_code == 201


def test_unknown_or_unpublished_places_cannot_be_bought(seeded):
    assert seeded.post(BUY, json=body(1, place="no-existe"), headers=hdr()).status_code == 404


def test_the_buyer_cannot_forge_the_price_or_the_owner(seeded):
    forged = {**body(1, 2), "unit_price_cents": 1, "total_cents": 1, "user_id": "otro", "status": "usado"}
    order = seeded.post(BUY, json=forged, headers=hdr()).get_json()
    assert order["total_cents"] == 1000 and order["status"] == "pagado"
    assert seeded.get(f"{BUY}/mine", headers=hdr(sub="otro")).get_json()["items"] == []


def test_my_tickets_lists_only_mine_newest_first(seeded):
    a = seeded.post(BUY, json=body(1), headers=hdr()).get_json()["code"]
    b = seeded.post(BUY, json=body(2), headers=hdr()).get_json()["code"]
    seeded.post(BUY, json=body(1), headers=hdr(sub="u-2", name="Luis"))
    mine = seeded.get(f"{BUY}/mine", headers=hdr()).get_json()["items"]
    assert [m["code"] for m in mine] == [b, a]


def test_a_member_can_cancel_a_future_ticket_but_not_someone_elses(seeded):
    code = seeded.post(BUY, json=body(3), headers=hdr()).get_json()["code"]
    assert seeded.post(f"{BUY}/cancel", json={"code": code}, headers=hdr(sub="u-2")).status_code == 404
    assert seeded.post(f"{BUY}/cancel", json={"code": code}, headers=hdr()).get_json()["status"] == "anulado"
    assert seeded.post(f"{BUY}/cancel", json={"code": code}, headers=hdr()).status_code == 409      # ya anulado


def test_a_ticket_for_today_cannot_be_cancelled(seeded):
    code = seeded.post(BUY, json=body(0), headers=hdr()).get_json()["code"]
    assert seeded.post(f"{BUY}/cancel", json={"code": code}, headers=hdr()).status_code == 409


def test_only_the_admin_validates_and_only_on_the_visit_day_and_once(seeded):
    today = seeded.post(BUY, json=body(0, 3), headers=hdr()).get_json()["code"]
    later = seeded.post(BUY, json=body(2), headers=hdr()).get_json()["code"]
    validate = f"{BUY[:-8]}/admin/tickets/validate"
    assert seeded.post(validate, json={"code": today}, headers=hdr()).status_code == 403
    assert seeded.post(validate, json={"code": today}).status_code == 401
    ok = seeded.post(validate, json={"code": today.lower()}, headers=hdr("admin", "a-1"))      # tolera minúsculas
    assert ok.status_code == 200 and ok.get_json()["status"] == "usado" and ok.get_json()["quantity"] == 3
    again = seeded.post(validate, json={"code": today}, headers=hdr("admin", "a-1"))
    assert again.status_code == 409 and "ya usado" in again.get_json()["detail"]
    other_day = seeded.post(validate, json={"code": later}, headers=hdr("admin", "a-1"))
    assert other_day.status_code == 409 and "no para hoy" in other_day.get_json()["detail"]
    assert seeded.post(validate, json={"code": "HN-ZZZZZZZZ"}, headers=hdr("admin", "a-1")).status_code == 404
    assert seeded.post(validate, json={"code": "malo"}, headers=hdr("admin", "a-1")).status_code == 422


def test_a_cancelled_ticket_is_not_valid_at_the_door(seeded):
    code = seeded.post(BUY, json=body(2), headers=hdr()).get_json()["code"]
    seeded.post(f"{BUY}/cancel", json={"code": code}, headers=hdr())
    r = seeded.post("/api/v1/admin/tickets/validate", json={"code": code}, headers=hdr("admin", "a-1"))
    assert r.status_code == 409 and "anulado" in r.get_json()["detail"]


def test_the_sales_summary_is_admin_only_and_adds_up(seeded):
    seeded.post(BUY, json=body(0, 2), headers=hdr())
    seeded.post(BUY, json=body(1, 3, place="cascada-san-miguel"), headers=hdr(sub="u-2"))
    cancelled = seeded.post(BUY, json=body(5, 4), headers=hdr()).get_json()["code"]
    seeded.post(f"{BUY}/cancel", json={"code": cancelled}, headers=hdr())
    url = "/api/v1/admin/tickets/summary"
    assert seeded.get(url, headers=hdr()).status_code == 403
    s = seeded.get(url, headers=hdr("admin", "a-1")).get_json()
    assert s["sold_total"] == {"orders": 2, "tickets": 5, "revenue_cents": 2500}          # lo anulado no cuenta
    assert s["sold_today"]["tickets"] == 5 and s["entering_today"]["tickets"] == 2
    assert [u["tickets"] for u in s["upcoming"]] == [2, 3]
    assert {p["place_name"] for p in s["by_place"]} == {"Catarata Velo de las Ninfas", "Cascada San Miguel"}


def test_the_service_is_what_applies_the_price(app, seeded):
    with app.app_context():
        order = tickets.purchase(VELO, visits.today_pe(), 4, "efectivo", "  Eva  ", "u-9")
    assert order["total_cents"] == 2000 and order["buyer_name"] == "Eva"
