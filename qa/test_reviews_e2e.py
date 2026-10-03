"""Opiniones, puntuación y moderación de punta a punta contra el sistema en marcha (sin mocks).

Las pruebas crean opiniones reales en la base de desarrollo y las eliminan al terminar. Las de moderación usan una
cuenta administradora temporal (ver ``admin_creds`` en conftest.py)."""

import concurrent.futures
import re
import time

import pytest
import requests

from conftest import BASE, CATALOG_URL, Web, unique_email

SLUG = "catarata-velo-de-las-ninfas"
OTHER = "cascada-san-miguel"
REVIEWS = f"/api/v1/places/{SLUG}/reviews"
MINE = f"{REVIEWS}/mine"
NAMES = [("Lucía", "Quispe"), ("Mateo", "Huamán"), ("Camila", "Rojas"), ("Diego", "Salazar"), ("Valeria", "Mendoza")]


def publish(web, rating=5, comment="Una cascada preciosa.", slug=SLUG):
    return web.api("PUT", f"/api/v1/places/{slug}/reviews/mine", json={"rating": rating, "comment": comment})


def public_list(slug=SLUG, **params):
    return requests.get(f"{BASE}/api/v1/places/{slug}/reviews", params=params, timeout=20)


def forget(web):
    for slug in (SLUG, OTHER):
        web.api("DELETE", f"/api/v1/places/{slug}/reviews/mine")


@pytest.fixture(scope="module")
def crowd():
    """Cinco visitantes registrados y con sesión (flujo real: registro, código por correo, ingreso)."""
    people = []
    for first, last in NAMES:
        web, email = Web(), unique_email("qa.reviews")
        assert web.register(email, first=first, last=last).status_code == 302
        assert web.verify(email).status_code == 302
        people.append(web)
    yield people
    for web in people:
        forget(web)


@pytest.fixture(autouse=True)
def clean(crowd):
    yield
    for web in crowd:
        forget(web)


@pytest.fixture()
def place_id(admin_token):
    resp = requests.get(f"{CATALOG_URL}/api/v1/admin/places", headers={"Authorization": f"Bearer {admin_token}"}, timeout=20)
    return next(p["id"] for p in resp.json()["items"] if p["slug"] == SLUG)


def review_id(web, slug=SLUG):
    return web.api("GET", f"/api/v1/places/{slug}/reviews/mine").json()["review"]["id"]


# --------------------------------------------------------------- visitantes
def test_places_expose_a_rating_summary():
    items = requests.get(f"{BASE}/api/v1/places", timeout=20).json()["items"]
    assert items and all({"average", "count"} <= set(p["rating"]) for p in items)


def test_a_visitor_rates_and_everyone_sees_it_without_personal_data(crowd):
    web = crowd[0]
    resp = publish(web, 5, "Agua cristalina y sendero seguro.")
    assert resp.status_code == 201 and resp.json()["review"]["author"] == "Lucía Q."
    anonymous = public_list()
    assert anonymous.status_code == 200 and anonymous.headers["Cache-Control"] == "no-store"
    body = anonymous.json()
    assert any(r["comment"] == "Agua cristalina y sendero seguro." for r in body["items"])
    raw = anonymous.text
    assert "Quispe" not in raw and "@example.com" not in raw and "qa.reviews" not in raw
    assert body["summary"]["count"] >= 1
    place = next(p for p in requests.get(f"{BASE}/api/v1/places", timeout=20).json()["items"] if p["slug"] == SLUG)
    assert place["rating"]["count"] >= 1


def test_edit_and_delete_own_review(crowd):
    web = crowd[1]
    assert publish(web, 3, "Regular.").status_code == 201
    edited = publish(web, 5, "Mejoró con la segunda visita.")
    assert edited.status_code == 200 and edited.json()["created"] is False
    assert web.api("GET", MINE).json()["review"]["rating"] == 5
    assert web.api("DELETE", MINE).status_code == 200
    assert web.api("GET", MINE).json()["review"] is None
    assert web.api("DELETE", MINE).status_code == 404


def test_guards_session_and_csrf_header(crowd):
    assert requests.put(f"{BASE}{MINE}", json={"rating": 5}, headers={"X-Requested-With": "XMLHttpRequest"},
                        timeout=20).status_code == 401
    assert requests.get(f"{BASE}{MINE}", timeout=20).status_code == 401
    forged = crowd[2].s.put(f"{BASE}{MINE}", json={"rating": 5, "comment": "desde otro sitio"}, timeout=20)
    assert forged.status_code == 403                                  # sin X-Requested-With
    assert crowd[2].api("GET", MINE).json()["review"] is None


@pytest.mark.parametrize("payload,field", [
    ({"rating": 0}, "rating"), ({"rating": 6}, "rating"), ({"rating": "5"}, "rating"), ({"rating": True}, "rating"),
    ({"rating": 5, "comment": "mira http://malo.example"}, "comment"), ({"rating": 5, "comment": "ab"}, "comment"),
    ({"rating": 5, "comment": "x" * 1001}, "comment"), ({"comment": "sin puntuación"}, "rating"),
])
def test_invalid_reviews_are_rejected_over_http(crowd, payload, field):
    resp = crowd[3].api("PUT", MINE, json=payload)
    assert resp.status_code == 422 and field in [e["field"] for e in resp.json()["errors"]]
    assert crowd[3].api("GET", MINE).json()["review"] is None


def test_a_gigantic_body_is_rejected_without_hurting_the_service(crowd):
    started = time.perf_counter()
    resp = crowd[3].api("PUT", MINE, json={"rating": 5, "comment": "a" * 200_000})
    assert resp.status_code in (413, 422) and time.perf_counter() - started < 3
    assert requests.get(f"{BASE}/readyz", timeout=10).status_code == 200


def test_simultaneous_publishing_by_one_person_never_fails(crowd):
    web = crowd[4]
    cookies = web.s.cookies.get_dict()

    def attempt(i):
        s = requests.Session()
        s.cookies.update(cookies)
        return s.put(f"{BASE}{MINE}", json={"rating": 1 + i % 5, "comment": f"Intento {i}."},
                     headers={"X-Requested-With": "XMLHttpRequest"}, timeout=30).status_code

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(attempt, range(8)))
    assert set(statuses) <= {200, 201}, statuses
    assert sum(1 for r in public_list(limit=50).json()["items"] if r["author"] == "Valeria M.") == 1


def test_listing_is_fast_and_paged(crowd):
    for web, stars in zip(crowd, (5, 4, 3, 2, 1)):
        publish(web, stars, f"Opinión de {stars} estrellas.")
    started = time.perf_counter()
    for _ in range(15):
        assert public_list(limit=3).status_code == 200
    assert (time.perf_counter() - started) / 15 < 0.5
    page = public_list(limit=2, offset=0).json()
    assert len(page["items"]) == 2 and page["total"] >= 5
    assert public_list(limit=0).status_code == 422 and public_list(offset=-1).status_code == 422


# ------------------------------------------------------------ moderación
def test_regular_visitors_cannot_moderate(crowd):
    publish(crowd[0], 5, "Mi opinión.")
    rid = review_id(crowd[0])
    assert crowd[1].api("DELETE", f"/api/v1/reviews/{rid}").status_code == 403
    assert crowd[1].api("PUT", f"/api/v1/reviews/{rid}/pin", json={"pinned": True}).status_code == 403
    assert requests.delete(f"{BASE}/api/v1/reviews/{rid}", headers={"X-Requested-With": "XMLHttpRequest"},
                           timeout=20).status_code == 401
    assert any(r["id"] == rid for r in public_list(limit=50).json()["items"])


def test_the_admin_pins_and_the_review_goes_first_then_unpins(crowd, admin_web):
    for web, text in zip(crowd[:3], ("Primera.", "Segunda.", "Tercera.")):
        publish(web, 4, text)
        time.sleep(0.05)
    oldest = review_id(crowd[0])
    pinned = admin_web.api("PUT", f"/api/v1/reviews/{oldest}/pin", json={"pinned": True})
    assert pinned.status_code == 200 and pinned.json()["pinned"] is True
    items = public_list(limit=50).json()["items"]
    assert items[0]["id"] == oldest and items[0]["pinned"] is True
    assert admin_web.api("PUT", f"/api/v1/reviews/{oldest}/pin", json={"pinned": False}).status_code == 200
    assert next(r for r in public_list(limit=50).json()["items"] if r["id"] == oldest)["pinned"] is False


def test_at_most_three_pinned_per_place(crowd, admin_web):
    for web in crowd[:4]:
        publish(web, 5, "Merece la pena.")
    ids = [review_id(w) for w in crowd[:4]]
    assert all(admin_web.api("PUT", f"/api/v1/reviews/{i}/pin", json={"pinned": True}).status_code == 200 for i in ids[:3])
    blocked = admin_web.api("PUT", f"/api/v1/reviews/{ids[3]}/pin", json={"pinned": True})
    assert blocked.status_code == 409 and blocked.json()["code"] == "PIN_LIMIT"
    assert admin_web.api("PUT", f"/api/v1/reviews/{ids[0]}/pin", json={"pinned": False}).status_code == 200
    assert admin_web.api("PUT", f"/api/v1/reviews/{ids[3]}/pin", json={"pinned": True}).status_code == 200


def test_the_admin_deletes_a_review_and_the_author_can_write_again(crowd, admin_web):
    publish(crowd[0], 1, "Comentario que incumple las normas.")
    rid = review_id(crowd[0])
    assert admin_web.api("DELETE", f"/api/v1/reviews/{rid}").status_code == 204
    assert all(r["id"] != rid for r in public_list(limit=50).json()["items"])
    assert crowd[0].api("GET", MINE).json()["review"] is None
    assert admin_web.api("DELETE", f"/api/v1/reviews/{rid}").status_code == 404
    assert publish(crowd[0], 4, "Segunda oportunidad.").status_code == 201


def test_moderation_is_audited_without_keeping_the_deleted_text(crowd, admin_web, admin_token, place_id):
    publish(crowd[0], 1, "Texto ofensivo QA que no debe conservarse.")
    rid = review_id(crowd[0])
    admin_web.api("PUT", f"/api/v1/reviews/{rid}/pin", json={"pinned": True})
    admin_web.api("DELETE", f"/api/v1/reviews/{rid}")
    history = requests.get(f"{CATALOG_URL}/api/v1/admin/places/{place_id}/history",
                           headers={"Authorization": f"Bearer {admin_token}"}, timeout=20).json()["items"]
    actions = [h["action"] for h in history]
    assert "review_pinned" in actions and "review_deleted" in actions
    assert "ofensivo QA" not in str(history)


def test_admin_page_lists_moderates_and_escapes_html(crowd, admin_web, place_id):
    payload = '<img src=x onerror=alert(1)> y <script>alert("qa")</script>'
    assert publish(crowd[0], 2, payload).status_code == 201
    html = admin_web.get(f"/admin/lugares/{place_id}").text
    assert 'id="opiniones"' in html and "Lucía Q." in html
    assert "<img src=x onerror" not in html and "<script>alert(\"qa\")" not in html and "&lt;img" in html
    rid = review_id(crowd[0])
    token = admin_web.csrf(f"/admin/lugares/{place_id}")
    pin = admin_web.s.post(f"{BASE}/admin/opiniones/{rid}/fijar", data={"csrf_token": token, "place_id": place_id, "pinned": "1"},
                           allow_redirects=False, timeout=20)
    assert pin.status_code == 302 and pin.headers["Location"].endswith("#opiniones")
    assert "Quitar de fijadas" in admin_web.get(f"/admin/lugares/{place_id}").text
    gone = admin_web.s.post(f"{BASE}/admin/opiniones/{rid}/eliminar", data={"csrf_token": token, "place_id": place_id},
                            allow_redirects=False, timeout=20)
    assert gone.status_code == 302 and review_id_or_none(crowd[0]) is None


def review_id_or_none(web):
    return web.api("GET", MINE).json()["review"]


def test_moderation_pages_reject_visitors_and_missing_csrf(crowd, admin_web, place_id):
    publish(crowd[0], 5, "Hola.")
    rid = review_id(crowd[0])
    visitor = crowd[1]
    resp = visitor.s.post(f"{BASE}/admin/opiniones/{rid}/eliminar", data={"csrf_token": visitor.csrf("/registro"), "place_id": place_id},
                          allow_redirects=False, timeout=20)
    assert resp.status_code == 403
    no_csrf = admin_web.s.post(f"{BASE}/admin/opiniones/{rid}/eliminar", data={"place_id": place_id},
                               allow_redirects=False, timeout=20)
    assert no_csrf.status_code == 400
    assert review_id_or_none(crowd[0]) is not None


def test_the_home_page_ships_the_reviews_ui(member_web):
    html = member_web.get("/").text
    assert 'id="reviews-dialog"' in html and "js/reviews.js" in html
    js = member_web.get("/static/js/reviews.js")
    assert js.status_code == 200 and re.search(r"esc\(r\.comment\)", js.text)
