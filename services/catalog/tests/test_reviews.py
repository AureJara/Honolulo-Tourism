"""Opiniones y puntuación de los lugares + moderación del administrador (fijar y eliminar)."""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import IntegrityError

from app import reviews
from app.extensions import db
from app.models import Place, PlaceAudit, PlaceReview
from conftest import token

SLUG = "catarata-velo-de-las-ninfas"
OTHER = "cascada-san-miguel"
REVIEWS = f"/api/v1/places/{SLUG}/reviews"
MINE = f"{REVIEWS}/mine"


def bearer(sub="u-1", name="Ana Ríos", role="user", **extra):
    return {"Authorization": f"Bearer {token(role, name=name, sub=sub, **extra)}"}


def put(client, rating=5, comment="Una cascada preciosa.", headers=None, slug=SLUG, **raw):
    body = {"rating": rating, "comment": comment}
    body.update(raw)
    return client.put(f"/api/v1/places/{slug}/reviews/mine", json=body, headers=headers or bearer())


def listing(client, slug=SLUG, **params):
    return client.get(f"/api/v1/places/{slug}/reviews", query_string=params)


def errors_of(resp):
    return {e["field"]: e["message"] for e in resp.get_json()["errors"]}


def review_id(client, who="u-1"):
    return client.get(MINE, headers=bearer(who)).get_json()["review"]["id"]


# ----------------------------------------------------------------- lectura pública
def test_empty_place_has_no_reviews_and_no_average(seeded):
    body = listing(seeded).get_json()
    assert body["items"] == [] and body["total"] == 0
    assert body["summary"] == {"average": None, "count": 0, "distribution": {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}}


def test_reading_reviews_needs_no_session_and_is_never_cached(seeded):
    resp = listing(seeded)
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"


def test_unknown_or_unpublished_places_have_no_reviews(seeded, admin):
    assert listing(seeded, slug="no-existe").status_code == 404
    assert put(seeded, slug="no-existe").status_code == 404
    place_id = seeded.get("/api/v1/admin/places", headers=admin).get_json()["items"][0]["id"]
    seeded.patch(f"/api/v1/admin/places/{place_id}", json={"status": "draft"}, headers=admin)
    assert listing(seeded).status_code == 404 and put(seeded).status_code == 404


def test_places_expose_their_average_rating(seeded):
    put(seeded, 5, headers=bearer("u-1"))
    put(seeded, 4, headers=bearer("u-2"))
    places = {p["slug"]: p for p in seeded.get("/api/v1/places").get_json()["items"]}
    assert places[SLUG]["rating"] == {"average": 4.5, "count": 2}
    assert places[OTHER]["rating"] == {"average": None, "count": 0}
    assert seeded.get(f"/api/v1/places/{SLUG}").get_json()["rating"]["count"] == 2


def test_summary_rounds_to_one_decimal_and_counts_each_star(seeded):
    for i, stars in enumerate([5, 5, 4, 2]):
        put(seeded, stars, headers=bearer(f"u-{i}"))
    summary = listing(seeded).get_json()["summary"]
    assert summary["average"] == 4.0 and summary["count"] == 4
    assert summary["distribution"] == {"5": 2, "4": 1, "3": 0, "2": 1, "1": 0}
    put(seeded, 4, headers=bearer("u-9"))
    assert listing(seeded).get_json()["summary"]["average"] == 4.0          # (5+5+4+2+4)/5
    put(seeded, 5, headers=bearer("u-10"))
    assert listing(seeded).get_json()["summary"]["average"] == 4.2          # 25/6 = 4.1666 → 4.2


# ------------------------------------------------------------------ publicar
def test_publishing_requires_a_session(seeded):
    assert seeded.put(MINE, json={"rating": 5}).status_code == 401
    assert seeded.get(MINE).status_code == 401
    assert seeded.delete(MINE).status_code == 401
    bad = seeded.put(MINE, json={"rating": 5}, headers={"Authorization": "Bearer abc.def.ghi"})
    assert bad.status_code == 401


def test_a_visitor_can_rate_and_comment(seeded):
    resp = put(seeded, 5, "Agua cristalina, volvería sin dudar.")
    body = resp.get_json()
    assert resp.status_code == 201 and body["created"] is True
    review = body["review"]
    assert review["rating"] == 5 and review["comment"] == "Agua cristalina, volvería sin dudar."
    assert review["author"] == "Ana R." and review["pinned"] is False
    assert body["summary"]["count"] == 1 and body["summary"]["average"] == 5.0
    assert listing(seeded).get_json()["items"][0]["id"] == review["id"]


def test_public_output_never_exposes_identifiers_or_contact_data(seeded):
    put(seeded, 5, "Hermoso lugar.", headers=bearer("secret-user-id", email="ana.privada@example.com"))
    raw = listing(seeded).get_data(as_text=True)
    assert "secret-user-id" not in raw and "ana.privada" not in raw and "Ríos" not in raw


def test_the_public_name_is_first_name_plus_surname_initial(seeded):
    put(seeded, 5, headers=bearer("u-1", name="María José Pérez Soto", first_name="María José"))
    assert listing(seeded).get_json()["items"][0]["author"] == "María José P."


@pytest.mark.parametrize("claims,expected", [
    ({"name": "Ana Ríos"}, "Ana R."), ({"name": "Madonna"}, "Madonna"), ({"name": ""}, "Visitante"),
    ({}, "Visitante"), ({"name": "Luis  ñandú"}, "Luis Ñ."), ({"name": "Ana 123"}, "Ana"),
    ({"name": "A" * 100 + " Zeta"}, ("A" * 100 + " Zeta")[:60]),
])
def test_author_display_rules(claims, expected):
    assert reviews.author_display(claims) == expected


def test_a_rating_without_comment_is_valid(seeded):
    for comment in (None, "", "   \n  "):
        resp = put(seeded, 4, comment)
        assert resp.status_code in (200, 201) and resp.get_json()["review"]["comment"] is None


def test_there_is_one_review_per_person_and_it_is_edited_not_duplicated(seeded, app):
    assert put(seeded, 3, "Regular.").status_code == 201
    again = put(seeded, 5, "Mejoró mucho con la segunda visita.")
    assert again.status_code == 200 and again.get_json()["created"] is False
    with app.app_context():
        assert db.session.execute(db.select(db.func.count()).select_from(PlaceReview)).scalar_one() == 1
    assert listing(seeded).get_json()["summary"]["average"] == 5.0


def test_the_same_person_can_review_different_places(seeded):
    assert put(seeded, 5, slug=SLUG).status_code == 201
    assert put(seeded, 3, slug=OTHER).status_code == 201


@pytest.mark.parametrize("rating", [0, 6, -1, 4.5, "5", None, True, False, [], {}, 10**9])
def test_invalid_ratings_are_rejected(seeded, rating):
    resp = put(seeded, rating)
    assert resp.status_code == 422 and "rating" in errors_of(resp)
    assert listing(seeded).get_json()["total"] == 0


def test_rating_is_required(seeded):
    resp = seeded.put(MINE, json={"comment": "Sin puntuación"}, headers=bearer())
    assert resp.status_code == 422 and "rating" in errors_of(resp)


@pytest.mark.parametrize("comment,fragment", [
    ("ab", "al menos 3"), ("x" * 1001, "1000"), ("Mira http://malo.example/ofertas", "enlaces"),
    ("visita www.sitio-raro.net ya", "enlaces"), ("escríbeme en tienda.com", "enlaces"),
    ("HTTPS://MAYUSCULAS.EXAMPLE", "enlaces"), (123, None), (["a"], None), ({"a": 1}, None),
])
def test_invalid_comments_are_rejected(seeded, comment, fragment):
    resp = put(seeded, 5, comment)
    assert resp.status_code == 422 and "comment" in errors_of(resp)
    if fragment:
        assert fragment in errors_of(resp)["comment"]


def test_a_comment_of_exactly_the_limit_is_accepted(seeded):
    assert put(seeded, 5, "a" * 1000).status_code == 201
    assert put(seeded, 5, "a" * 1001).status_code == 422


def test_text_is_cleaned_of_control_and_invisible_characters(seeded):
    resp = put(seeded, 5, "Hola\x00\x07 mundo‮​ esto﻿ es\r\n\n\n\n\nbonito  ")
    comment = resp.get_json()["review"]["comment"]
    assert comment == "Hola mundo esto es\r\n\nbonito" or comment == "Hola mundo esto es\n\nbonito"
    assert all(ch not in comment for ch in ("\x00", "\x07", "‮", "​", "﻿"))


def test_html_is_stored_as_inert_text_and_returned_verbatim(seeded):
    payload = '<img src=x onerror=alert(1)> <script>alert("x")</script>'
    assert put(seeded, 5, payload).status_code == 201
    resp = listing(seeded)
    assert resp.get_json()["items"][0]["comment"] == payload              # el escape lo hace la interfaz
    assert resp.mimetype == "application/json" and resp.headers["X-Content-Type-Options"] == "nosniff"


def test_a_gigantic_comment_is_rejected_quickly(seeded):
    started = time.perf_counter()
    resp = put(seeded, 5, "a" * 300_000)
    assert resp.status_code == 422 and time.perf_counter() - started < 2


def test_body_must_be_a_json_object(seeded):
    assert seeded.put(MINE, data="rating=5", headers=bearer()).status_code == 400
    assert seeded.put(MINE, json=[5], headers=bearer()).status_code == 400
    assert seeded.put(MINE, json="5", headers=bearer()).status_code == 400


def test_extra_fields_cannot_pin_or_impersonate(seeded, app):
    resp = put(seeded, 5, "Intento de trampa.", pinned=True, pinned_at="2026-01-01", author="Administrador Honolulo",
               user_id="otra-persona", id=str(uuid.uuid4()), role="admin")
    review = resp.get_json()["review"]
    assert review["pinned"] is False and review["author"] == "Ana R."
    with app.app_context():
        row = db.session.execute(db.select(PlaceReview)).scalar_one()
        assert row.user_id == "u-1" and not row.pinned


def test_my_review_is_private_to_me(seeded):
    assert seeded.get(MINE, headers=bearer()).get_json() == {"review": None}
    put(seeded, 4, "Buena experiencia.")
    mine = seeded.get(MINE, headers=bearer()).get_json()["review"]
    assert mine["rating"] == 4
    assert seeded.get(MINE, headers=bearer("u-2")).get_json() == {"review": None}


def test_a_visitor_can_delete_only_their_own_review(seeded):
    put(seeded, 4, "Mi opinión", headers=bearer("u-1"))
    put(seeded, 2, "La de otra persona", headers=bearer("u-2"))
    gone = seeded.delete(MINE, headers=bearer("u-1"))
    assert gone.status_code == 200 and gone.get_json()["summary"]["count"] == 1
    assert seeded.delete(MINE, headers=bearer("u-1")).status_code == 404
    items = listing(seeded).get_json()["items"]
    assert [i["comment"] for i in items] == ["La de otra persona"]


def test_a_regular_user_cannot_use_moderation_endpoints(seeded):
    put(seeded, 5, "Hola")
    rid = review_id(seeded)
    assert seeded.delete(f"/api/v1/admin/reviews/{rid}", headers=bearer()).status_code == 403
    assert seeded.put(f"/api/v1/admin/reviews/{rid}/pin", json={"pinned": True}, headers=bearer()).status_code == 403
    assert seeded.delete(f"/api/v1/admin/reviews/{rid}").status_code == 401
    assert seeded.put(f"/api/v1/admin/reviews/{rid}/pin", json={"pinned": True}).status_code == 401
    assert listing(seeded).get_json()["total"] == 1


# ----------------------------------------------------------------- orden y páginas
def test_newest_reviews_come_first_and_pages_work(seeded, app):
    for i in range(5):
        put(seeded, 5, f"Opinión número {i}.", headers=bearer(f"u-{i}"))
        time.sleep(0.01)
    first = listing(seeded, limit=2).get_json()
    assert [i["comment"] for i in first["items"]] == ["Opinión número 4.", "Opinión número 3."]
    assert first["total"] == 5 and first["limit"] == 2 and first["offset"] == 0
    third = listing(seeded, limit=2, offset=4).get_json()
    assert [i["comment"] for i in third["items"]] == ["Opinión número 0."]
    assert listing(seeded, offset=50).get_json()["items"] == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"limit": "abc"}, {"offset": -1}, {"offset": "x"},
                                    {"limit": 1.5}, {"offset": 10**9}])
def test_invalid_paging_is_rejected(seeded, params):
    assert listing(seeded, **params).status_code == 422


# -------------------------------------------------------------------- moderación
def make_reviews(client, n):
    for i in range(n):
        put(client, 3 + i % 3, f"Opinión de la persona {i}.", headers=bearer(f"u-{i}", name=f"Persona{i} Apellido"))
        time.sleep(0.01)


def pin(client, headers, rid, pinned=True):
    return client.put(f"/api/v1/admin/reviews/{rid}/pin", json={"pinned": pinned}, headers=headers)


def test_the_admin_can_pin_a_review_and_it_goes_first(seeded, admin):
    make_reviews(seeded, 3)
    oldest = review_id(seeded, "u-0")
    resp = pin(seeded, admin, oldest)
    assert resp.status_code == 200 and resp.get_json()["pinned"] is True
    items = listing(seeded).get_json()["items"]
    assert items[0]["id"] == oldest and items[0]["pinned"] is True
    assert [i["pinned"] for i in items] == [True, False, False]


def test_pinned_reviews_are_ordered_by_when_they_were_pinned(seeded, admin):
    make_reviews(seeded, 3)
    a, b = review_id(seeded, "u-0"), review_id(seeded, "u-1")
    pin(seeded, admin, a)
    time.sleep(0.02)
    pin(seeded, admin, b)
    assert [i["id"] for i in listing(seeded).get_json()["items"][:2]] == [b, a]


def test_unpinning_returns_the_review_to_its_chronological_place(seeded, admin):
    make_reviews(seeded, 3)
    oldest = review_id(seeded, "u-0")
    pin(seeded, admin, oldest)
    resp = pin(seeded, admin, oldest, pinned=False)
    assert resp.status_code == 200 and resp.get_json()["pinned"] is False
    assert listing(seeded).get_json()["items"][-1]["id"] == oldest


def test_pinning_is_idempotent_and_audited_once(seeded, admin):
    make_reviews(seeded, 1)
    rid = review_id(seeded, "u-0")
    assert pin(seeded, admin, rid).status_code == 200
    assert pin(seeded, admin, rid).status_code == 200
    place_id = seeded.get("/api/v1/admin/places", headers=admin).get_json()["items"][0]["id"]
    actions = [h["action"] for h in seeded.get(f"/api/v1/admin/places/{place_id}/history", headers=admin).get_json()["items"]]
    assert actions.count("review_pinned") == 1


def test_at_most_three_reviews_can_be_pinned_per_place(seeded, admin):
    make_reviews(seeded, 5)
    ids = [review_id(seeded, f"u-{i}") for i in range(5)]
    assert all(pin(seeded, admin, rid).status_code == 200 for rid in ids[:3])
    blocked = pin(seeded, admin, ids[3])
    assert blocked.status_code == 409 and blocked.get_json()["code"] == "PIN_LIMIT"
    assert pin(seeded, admin, ids[0], pinned=False).status_code == 200
    assert pin(seeded, admin, ids[3]).status_code == 200


def test_the_pin_limit_is_per_place(seeded, admin):
    for i in range(4):
        put(seeded, 5, f"Opinión {i}.", headers=bearer(f"u-{i}"))
        put(seeded, 5, f"Opinión {i}.", headers=bearer(f"u-{i}"), slug=OTHER)
    ids = [review_id(seeded, f"u-{i}") for i in range(3)]
    assert all(pin(seeded, admin, rid).status_code == 200 for rid in ids)
    other_id = seeded.get(f"/api/v1/places/{OTHER}/reviews/mine", headers=bearer("u-0")).get_json()["review"]["id"]
    assert pin(seeded, admin, other_id).status_code == 200


def test_concurrent_pins_never_exceed_the_limit(seeded, admin, app):
    make_reviews(seeded, 8)
    ids = [review_id(seeded, f"u-{i}") for i in range(8)]

    def attempt(rid):
        return app.test_client().put(f"/api/v1/admin/reviews/{rid}/pin", json={"pinned": True}, headers=admin).status_code

    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(attempt, ids))
    assert sorted(statuses).count(200) == 3 and statuses.count(409) == 5
    with app.app_context():
        assert db.session.execute(db.select(db.func.count()).select_from(PlaceReview)
                                  .where(PlaceReview.pinned)).scalar_one() == 3


@pytest.mark.parametrize("body", [{}, {"pinned": "yes"}, {"pinned": 1}, {"pinned": None}, []])
def test_pin_body_is_validated(seeded, admin, body):
    make_reviews(seeded, 1)
    resp = seeded.put(f"/api/v1/admin/reviews/{review_id(seeded, 'u-0')}/pin", json=body, headers=admin)
    assert resp.status_code in (400, 422)


def test_the_admin_can_delete_any_review_and_the_summary_updates(seeded, admin):
    make_reviews(seeded, 3)
    victim = review_id(seeded, "u-1")
    resp = seeded.delete(f"/api/v1/admin/reviews/{victim}", headers=admin)
    assert resp.status_code == 204
    body = listing(seeded).get_json()
    assert victim not in [i["id"] for i in body["items"]] and body["total"] == 2
    assert seeded.delete(f"/api/v1/admin/reviews/{victim}", headers=admin).status_code == 404


def test_deleting_leaves_an_audit_trail_without_the_comment_text(seeded, admin):
    put(seeded, 1, "Texto ofensivo que no debe conservarse.", headers=bearer("u-7"))
    seeded.delete(f"/api/v1/admin/reviews/{review_id(seeded, 'u-7')}", headers=admin)
    place_id = seeded.get("/api/v1/admin/places", headers=admin).get_json()["items"][0]["id"]
    history = seeded.get(f"/api/v1/admin/places/{place_id}/history", headers=admin).get_json()["items"]
    entry = next(h for h in history if h["action"] == "review_deleted")
    assert entry["actor_id"] == "admin-1" and entry["changes"]["author_id"] == "u-7" and entry["changes"]["rating"] == 1
    assert "ofensivo" not in str(history)


def test_a_deleted_review_frees_the_person_to_write_a_new_one(seeded, admin):
    put(seeded, 1, "Mala.")
    seeded.delete(f"/api/v1/admin/reviews/{review_id(seeded)}", headers=admin)
    assert put(seeded, 4, "Segunda oportunidad.").status_code == 201


def test_moderation_endpoints_handle_unknown_ids(seeded, admin):
    missing = uuid.uuid4()
    assert seeded.delete(f"/api/v1/admin/reviews/{missing}", headers=admin).status_code == 404
    assert pin(seeded, admin, missing).status_code == 404
    assert seeded.delete("/api/v1/admin/reviews/no-es-uuid", headers=admin).status_code == 404


def test_editing_the_text_of_a_pinned_review_unpins_it_but_a_rating_change_does_not(seeded, admin):
    make_reviews(seeded, 1)
    rid = review_id(seeded, "u-0")
    pin(seeded, admin, rid)
    same_text = listing(seeded).get_json()["items"][0]["comment"]
    put(seeded, 1, same_text, headers=bearer("u-0", name="Persona0 Apellido"))
    assert listing(seeded).get_json()["items"][0]["pinned"] is True
    put(seeded, 1, "Texto nuevo sin revisar.", headers=bearer("u-0", name="Persona0 Apellido"))
    assert listing(seeded).get_json()["items"][0]["pinned"] is False


def test_admins_can_review_places_too(seeded, admin):
    assert put(seeded, 5, "Probé la ruta personalmente.", headers=admin).status_code == 201
    assert listing(seeded).get_json()["items"][0]["author"] == "Admin H."


# ------------------------------------------------------------------ concurrencia
def test_simultaneous_first_reviews_from_one_person_make_one_row(seeded, app):
    def attempt(i):
        return app.test_client().put(MINE, json={"rating": 1 + i % 5, "comment": f"Intento {i}."},
                                     headers=bearer()).status_code

    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(attempt, range(8)))
    assert set(statuses) <= {200, 201} and statuses.count(201) == 1
    with app.app_context():
        assert db.session.execute(db.select(db.func.count()).select_from(PlaceReview)).scalar_one() == 1


# --------------------------------------------------------- restricciones en la base
def test_database_constraints_protect_the_data_even_without_the_api(seeded, app):
    with app.app_context():
        place = db.session.execute(db.select(Place)).scalars().first()

        def insert(**fields):
            base = dict(place_id=place.id, user_id=f"x-{uuid.uuid4().hex[:6]}", author_name="Ana R.", rating=5)
            base.update(fields)
            db.session.add(PlaceReview(**base))
            try:
                db.session.commit()
                return True
            except IntegrityError:
                db.session.rollback()
                return False

        assert insert(rating=0) is False and insert(rating=6) is False
        assert insert(comment="ab") is False and insert(comment="x" * 1001) is False
        assert insert(pinned=True) is False                    # fijada sin fecha
        assert insert(user_id="repetido") is True and insert(user_id="repetido") is False
        assert insert() is True


def test_deleting_a_place_cascades_to_its_reviews(seeded, app):
    put(seeded, 5, "Hola.")
    with app.app_context():
        db.session.execute(db.delete(Place).where(Place.slug == SLUG))
        db.session.commit()
        assert db.session.execute(db.select(db.func.count()).select_from(PlaceReview)).scalar_one() == 0
        assert db.session.execute(db.select(db.func.count()).select_from(PlaceAudit)).scalar_one() == 0


def test_the_review_domain_works_without_an_http_request(seeded, app):
    """La lógica de opiniones recibe quién actúa como parámetro: no depende de ``flask.g`` ni de la petición."""
    claims, moderator = {"sub": "u-77", "name": "Luz Vega"}, {"sub": "admin-9", "name": "Ada Min"}
    with app.app_context():
        place = db.session.execute(db.select(Place)).scalars().first()
        review, created = reviews.upsert_review(place, claims, {"rating": 4, "comment": "Directo, sin HTTP."})
        assert created and review.author_name == "Luz V." and review.user_id == "u-77"
        reviews.moderator_pin_review(review, True, moderator)
        assert review.pinned and review.pinned_by == "admin-9"
        reviews.moderator_delete_review(review, moderator)
        audit = db.session.execute(db.select(PlaceAudit).order_by(PlaceAudit.id)).scalars().all()
        assert [(a.action, a.actor_id) for a in audit] == [("review_pinned", "admin-9"), ("review_deleted", "admin-9")]
        assert reviews.get_my_review(place, "u-77") is None
