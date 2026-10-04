"""Opiniones y puntuación en la web: pasarela de la API, moderación del administrador, políticas y cabeceras."""

import json
import re
import uuid

import pytest
import requests

from conftest import ADMIN, AUTH, CATALOG, USER, csrf_from, login_cookies
from test_admin import PLACE, PLACE_ID, as_admin

SLUG = "cascada-san-miguel"
RID = str(uuid.uuid4())
XHR = {"X-Requested-With": "XMLHttpRequest"}
SUMMARY = {"average": 4.5, "count": 2, "distribution": {"5": 1, "4": 1, "3": 0, "2": 0, "1": 0}}
REVIEW = {"id": RID, "author": "Ana R.", "rating": 5, "comment": "Hermoso", "pinned": False,
          "created_at": "2026-10-03T12:00:00+00:00"}


def calls_to_catalog(mocked):
    return [c for c in mocked.calls if "catalog.test" in c.request.url]


# ----------------------------------------------------------------- lectura pública
def test_reviews_list_is_public_and_never_cached(client, mocked):
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews", json={"items": [REVIEW], "total": 1, "summary": SUMMARY})
    resp = client.get(f"/api/v1/places/{SLUG}/reviews?limit=5&offset=0&evil=1")
    assert resp.status_code == 200 and resp.get_json()["items"][0]["author"] == "Ana R."
    assert resp.headers["Cache-Control"] == "no-store"
    request = mocked.calls[0].request
    assert "Authorization" not in request.headers and "limit=5" in request.url and "evil" not in request.url


@pytest.mark.parametrize("bad", ["..%2f..%2fadmin", "A B", "x'%20OR%201=1", "a" * 81, "UPPER"])
def test_review_routes_validate_the_slug_before_calling_the_catalog(client, mocked, bad):
    login_cookies(client)
    assert client.get(f"/api/v1/places/{bad}/reviews").status_code == 404
    assert client.get(f"/api/v1/places/{bad}/reviews/mine").status_code == 404
    assert client.put(f"/api/v1/places/{bad}/reviews/mine", json={"rating": 5}, headers=XHR).status_code == 404
    assert calls_to_catalog(mocked) == []


def test_reviews_when_the_catalog_is_down(client, mocked):
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews", body=requests.ConnectionError("x"))
    resp = client.get(f"/api/v1/places/{SLUG}/reviews")
    assert resp.status_code == 503 and "opiniones no están disponibles" in resp.get_json()["detail"]


# --------------------------------------------------------------------- mi opinión
def test_my_review_needs_a_session(client, mocked):
    assert client.get(f"/api/v1/places/{SLUG}/reviews/mine").status_code == 401
    assert client.put(f"/api/v1/places/{SLUG}/reviews/mine", json={"rating": 5}, headers=XHR).status_code == 401
    assert client.delete(f"/api/v1/places/{SLUG}/reviews/mine", headers=XHR).status_code == 401
    assert calls_to_catalog(mocked) == []


@pytest.mark.parametrize("method", ["put", "delete"])
def test_mutations_without_the_ajax_header_are_rejected(client, mocked, method):
    login_cookies(client)
    resp = getattr(client, method)(f"/api/v1/places/{SLUG}/reviews/mine", json={"rating": 5})
    assert resp.status_code == 403 and resp.get_json()["code"] == "CSRF_REJECTED"
    assert calls_to_catalog(mocked) == []


def test_publishing_forwards_the_session_and_the_json(client, mocked):
    mocked.put(f"{CATALOG}/api/v1/places/{SLUG}/reviews/mine", status=201,
               json={"review": REVIEW, "created": True, "summary": SUMMARY})
    login_cookies(client)
    resp = client.put(f"/api/v1/places/{SLUG}/reviews/mine", json={"rating": 5, "comment": "Hermoso"}, headers=XHR)
    assert resp.status_code == 201 and resp.get_json()["created"] is True
    sent = mocked.calls[-1].request
    assert sent.headers["Authorization"] == "Bearer access-1"
    assert json.loads(sent.body) == {"rating": 5, "comment": "Hermoso"}
    assert resp.headers["Cache-Control"] == "no-store"


def test_validation_errors_from_the_catalog_reach_the_browser(client, mocked):
    mocked.put(f"{CATALOG}/api/v1/places/{SLUG}/reviews/mine", status=422,
               json={"code": "VALIDATION_ERROR", "detail": "Revisa los campos marcados.",
                     "errors": [{"field": "rating", "message": "Elige de 1 a 5 estrellas."}]})
    login_cookies(client)
    resp = client.put(f"/api/v1/places/{SLUG}/reviews/mine", json={"rating": 9}, headers=XHR)
    assert resp.status_code == 422 and resp.get_json()["errors"][0]["field"] == "rating"


def test_an_expired_session_is_renewed_transparently(client, mocked):
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews/mine", status=401, json={"code": "TOKEN_EXPIRED"})
    mocked.post(f"{AUTH}/api/v1/auth/refresh", json={"access_token": "access-2", "refresh_token": "refresh-2",
                                                     "expires_in": 900, "user": USER})
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews/mine", json={"review": None})
    login_cookies(client)
    resp = client.get(f"/api/v1/places/{SLUG}/reviews/mine")
    assert resp.status_code == 200 and resp.get_json() == {"review": None}
    assert any(h.startswith("hn_access=access-2") for h in resp.headers.getlist("Set-Cookie"))


def test_a_dead_session_clears_the_cookies(client, mocked):
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews/mine", status=401, json={"code": "AUTH_REQUIRED"})
    mocked.post(f"{AUTH}/api/v1/auth/refresh", status=401, json={"code": "INVALID_REFRESH_TOKEN"})
    login_cookies(client)
    resp = client.get(f"/api/v1/places/{SLUG}/reviews/mine")
    assert resp.status_code == 401
    assert any("hn_access=;" in h for h in resp.headers.getlist("Set-Cookie"))


# ---------------------------------------------------------------------- moderación
def test_moderation_endpoints_need_session_and_header(client, mocked):
    assert client.delete(f"/api/v1/reviews/{RID}", headers=XHR).status_code == 401
    assert client.put(f"/api/v1/reviews/{RID}/pin", json={"pinned": True}, headers=XHR).status_code == 401
    login_cookies(client)
    assert client.delete(f"/api/v1/reviews/{RID}").status_code == 403
    assert client.put(f"/api/v1/reviews/{RID}/pin", json={"pinned": True}).status_code == 403
    assert calls_to_catalog(mocked) == []


def test_ids_are_validated_before_reaching_the_catalog(client, mocked):
    login_cookies(client)
    for bad in ("no-es-uuid", "1; DROP TABLE", "../places", "x" * 100):
        assert client.delete(f"/api/v1/reviews/{bad}", headers=XHR).status_code == 404
        assert client.put(f"/api/v1/reviews/{bad}/pin", json={"pinned": True}, headers=XHR).status_code == 404
    assert calls_to_catalog(mocked) == []


def test_pin_and_delete_are_forwarded_to_the_admin_endpoints(client, mocked):
    mocked.put(f"{CATALOG}/api/v1/admin/reviews/{RID}/pin", json={**REVIEW, "pinned": True})
    mocked.delete(f"{CATALOG}/api/v1/admin/reviews/{RID}", status=204)
    login_cookies(client)
    pin = client.put(f"/api/v1/reviews/{RID}/pin", json={"pinned": True}, headers=XHR)
    assert pin.status_code == 200 and pin.get_json()["pinned"] is True
    gone = client.delete(f"/api/v1/reviews/{RID}", headers=XHR)
    assert gone.status_code == 204 and gone.data == b""


def test_a_regular_user_gets_the_catalogs_403_for_moderation(client, mocked):
    mocked.delete(f"{CATALOG}/api/v1/admin/reviews/{RID}", status=403,
                  json={"code": "FORBIDDEN", "detail": "No tienes permisos para esta operación."})
    login_cookies(client)
    resp = client.delete(f"/api/v1/reviews/{RID}", headers=XHR)
    assert resp.status_code == 403 and resp.get_json()["code"] == "FORBIDDEN"


# ------------------------------------------------- páginas de administración (HTML)
REVIEWS_BODY = {"summary": SUMMARY, "total": 2, "limit": 50, "offset": 0, "items": [
    {**REVIEW, "id": RID, "pinned": True, "author": "Ana R."},
    {"id": str(uuid.uuid4()), "author": "Luis M.", "rating": 3, "comment": None, "pinned": False,
     "created_at": "2026-10-02T09:00:00+00:00"}]}


def open_edit_page(client, mocked, reviews=REVIEWS_BODY, reviews_status=200):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": []})
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews", json=reviews, status=reviews_status)
    return client.get(f"/admin/lugares/{PLACE_ID}")


def test_the_edit_page_lists_reviews_with_moderation_buttons(client, mocked):
    html = open_edit_page(client, mocked).get_data(as_text=True)
    assert 'id="opiniones"' in html and "Opiniones de los visitantes" in html
    assert "Ana R." in html and "Luis M." in html and "Fijada" in html
    assert "Quitar de fijadas" in html and ">Fijar<" in html.replace("\n", "")
    assert f"/admin/opiniones/{RID}/eliminar" in html and f"/admin/opiniones/{RID}/fijar" in html
    assert "Solo puntuación, sin comentario." in html


def test_review_text_is_escaped_in_the_admin_page(client, mocked):
    evil = {**REVIEWS_BODY, "items": [{**REVIEW, "comment": '<script>alert("x")</script>', "author": "<b>Eva</b> Q."}]}
    html = open_edit_page(client, mocked, reviews=evil).get_data(as_text=True)
    assert "<script>alert" not in html and "&lt;script&gt;" in html and "<b>Eva</b>" not in html


def test_the_edit_page_still_works_when_reviews_cannot_be_loaded(client, mocked):
    resp = open_edit_page(client, mocked, reviews={"detail": "x"}, reviews_status=503)
    assert resp.status_code == 200 and "Cascada San Miguel" in resp.get_data(as_text=True)
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews", body=requests.ConnectionError("x"))
    assert client.get(f"/admin/lugares/{PLACE_ID}").status_code == 200


def test_a_draft_place_explains_when_reviews_open(client, mocked):
    draft = {**PLACE, "status": "draft"}
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=draft)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": []})
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews", status=404, json={"code": "NOT_FOUND"})
    assert "se abren cuando el lugar está publicado" in client.get(f"/admin/lugares/{PLACE_ID}").get_data(as_text=True)


def test_history_labels_moderation_actions(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews", json=REVIEWS_BODY)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": [
        {"id": 1, "action": a, "actor_name": "Admin Honolulo", "changes": {}, "created_at": "2026-10-03T12:00:00+00:00"}
        for a in ("review_deleted", "review_pinned", "review_unpinned")]})
    html = client.get(f"/admin/lugares/{PLACE_ID}").get_data(as_text=True)
    assert "eliminó una opinión" in html and "fijó una opinión" in html and "quitó una opinión de las fijadas" in html


def test_admin_pins_and_deletes_with_csrf(client, mocked):
    as_admin(client, mocked)
    token = csrf_from(client, "/registro")
    mocked.put(f"{CATALOG}/api/v1/admin/reviews/{RID}/pin", json=REVIEW)
    mocked.delete(f"{CATALOG}/api/v1/admin/reviews/{RID}", status=204)

    pin = client.post(f"/admin/opiniones/{RID}/fijar", data={"csrf_token": token, "place_id": PLACE_ID, "pinned": "1"})
    assert pin.status_code == 302 and pin.headers["Location"].endswith(f"/admin/lugares/{PLACE_ID}#opiniones")
    assert json.loads([c for c in mocked.calls if c.request.method == "PUT"][-1].request.body) == {"pinned": True}
    unpin = client.post(f"/admin/opiniones/{RID}/fijar", data={"csrf_token": token, "place_id": PLACE_ID, "pinned": "0"})
    assert unpin.status_code == 302
    assert json.loads([c for c in mocked.calls if c.request.method == "PUT"][-1].request.body) == {"pinned": False}
    gone = client.post(f"/admin/opiniones/{RID}/eliminar", data={"csrf_token": token, "place_id": PLACE_ID})
    assert gone.status_code == 302 and mocked.calls[-1].request.method == "DELETE"


def test_pin_limit_message_reaches_the_admin(client, mocked):
    as_admin(client, mocked)
    token = csrf_from(client, "/registro")
    mocked.put(f"{CATALOG}/api/v1/admin/reviews/{RID}/pin", status=409,
               json={"code": "PIN_LIMIT", "detail": "Solo se pueden fijar hasta 3 opiniones por lugar."})
    resp = client.post(f"/admin/opiniones/{RID}/fijar", data={"csrf_token": token, "place_id": PLACE_ID, "pinned": "1"},
                       follow_redirects=False)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert ("error", "Solo se pueden fijar hasta 3 opiniones por lugar.") in sess["_flashes"]


@pytest.mark.parametrize("action", ["fijar", "eliminar"])
def test_moderation_forms_require_login_admin_role_and_csrf(client, mocked, action):
    path = f"/admin/opiniones/{RID}/{action}"
    assert client.post(path).status_code == 302 and client.post(path).headers["Location"].startswith("/ingresar")
    as_admin(client, mocked, user=USER)
    token = csrf_from(client, "/registro")
    assert client.post(path, data={"csrf_token": token, "place_id": PLACE_ID}).status_code == 403
    mocked.reset()
    as_admin(client, mocked)
    assert client.post(path, data={"place_id": PLACE_ID}).status_code == 400            # sin CSRF
    assert calls_to_catalog(mocked) == []


def test_moderation_forms_reject_bad_ids(client, mocked):
    as_admin(client, mocked)
    token = csrf_from(client, "/registro")
    for action in ("fijar", "eliminar"):
        assert client.post(f"/admin/opiniones/no-es-uuid/{action}", data={"csrf_token": token}).status_code == 404
    assert calls_to_catalog(mocked) == []


# ------------------------------------------------------------- pantalla principal
def test_home_has_the_reviews_dialog_and_script(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", json=USER)
    login_cookies(client)
    html = client.get("/").get_data(as_text=True)
    assert 'id="reviews-dialog"' in html and "js/reviews.js" in html and 'data-role="user"' in html


def test_home_marks_the_admin_role_for_the_moderation_buttons(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", json=ADMIN)
    login_cookies(client)
    assert 'data-role="admin"' in client.get("/").get_data(as_text=True)


def test_static_scripts_are_served(client):
    for path in ("/static/js/reviews.js", "/static/js/app.js"):
        resp = client.get(path)
        assert resp.status_code == 200 and "javascript" in resp.mimetype
    js = client.get("/static/js/reviews.js").get_data(as_text=True)
    assert "innerHTML" in js and "esc(r.comment)" in js and "esc(r.author)" in js     # el texto del usuario se escapa


# ----------------------------------------------------------------------- políticas
def test_cookie_policy_lists_every_cookie_the_site_sets(client):
    html = client.get("/politica-de-cookies").get_data(as_text=True)
    for name in ("hn_access", "hn_refresh", "hn_csrf", "hn_session"):
        assert name in html
    assert "solo cookies esenciales" in html and "Cookies que NO usamos" in html and "hn_aviso_cookies" in html
    assert "15 minutos" in html and "7 días" in html and "Google Fonts" in html
    assert "revisado por asesoría legal" in html and "Versión 2026-10-04" in html


def test_the_site_sets_no_cookie_outside_the_documented_ones(client, mocked):
    login_cookies(client)
    mocked.get(f"{AUTH}/api/v1/auth/me", json=USER)
    names = set()
    for path in ("/ingresar", "/registro", "/politica-de-privacidad", "/politica-de-cookies"):
        for header in client.get(path).headers.getlist("Set-Cookie"):
            names.add(header.split("=", 1)[0])
    assert names <= {"hn_csrf", "hn_session"}


def test_privacy_policy_covers_reviews_email_security_and_rights(client):
    html = client.get("/politica-de-privacidad").get_data(as_text=True)
    for expected in ("Versión 2026-10-04", "Opiniones y puntuaciones", "inicial de tu apellido", "hash",
                     "Proveedor de correo", "Open-Meteo", "Google Fonts", "fijar", "eliminar", "Ley N.° 29733",
                     "derechos ARCO", "Menores de edad", "política de cookies", "nunca</strong> te pedirá tu contraseña"):
        assert expected in html, expected
    assert "revisado por asesoría legal" in html


def test_privacy_and_cookie_pages_link_to_each_other_and_the_footer_to_both(client):
    assert "/politica-de-cookies" in client.get("/politica-de-privacidad").get_data(as_text=True)
    assert "/politica-de-privacidad" in client.get("/politica-de-cookies").get_data(as_text=True)
    for path in ("/ingresar", "/registro"):
        html = client.get(path).get_data(as_text=True)
        assert "/politica-de-privacidad" in html and "/politica-de-cookies" in html


def test_cookie_notice_is_informative_and_dismissible(client):
    html = client.get("/ingresar").get_data(as_text=True)
    assert 'id="cookie-notice"' in html and 'role="region"' in html and "solo cookies esenciales" in html
    assert "localStorage" in html and "hn_aviso_cookies" in html
    assert re.search(r'id="cookie-notice"[^>]*\bhidden\b', html)                    # oculto hasta que el script decide


def test_register_form_states_the_new_password_rule(client):
    html = client.get("/registro").get_data(as_text=True)
    assert "Mínimo 10 caracteres" in html and "Mínimo 8" not in html


# ----------------------------------------------------------- cabeceras y secretos
def test_security_headers_are_present_everywhere(client):
    for path in ("/ingresar", "/politica-de-cookies", "/healthz"):
        h = client.get(path).headers
        assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
        csp = h["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp and "form-action 'self'" in csp and "base-uri 'self'" in csp
        assert "camera=()" in h["Permissions-Policy"] and h["Cross-Origin-Opener-Policy"] == "same-origin"
        assert "Strict-Transport-Security" not in h              # HSTS solo cuando se sirve por HTTPS


def test_hsts_is_sent_when_cookies_are_secure(app):
    app.config["COOKIE_SECURE"] = True
    h = app.test_client().get("/ingresar").headers
    assert "max-age=31536000" in h["Strict-Transport-Security"]


def test_the_web_refuses_to_start_without_a_secret_key():
    from app import create_app
    with pytest.raises(RuntimeError, match="SECRET_KEY no está configurado"):
        create_app({"TESTING": True, "SECRET_KEY": ""})


def test_production_rejects_weak_or_default_secrets():
    from app import create_app
    for weak in ("dev-web-secret-change-me", "short", "password", "x" * 31):
        with pytest.raises(RuntimeError, match="demasiado débil"):
            create_app({"SECRET_KEY": weak})
    assert create_app({"SECRET_KEY": "k" * 48})


# ------------------------------------------------------------- regresiones de la revisión manual
def test_modal_scroll_lock_does_not_depend_on_a_javascript_class(client):
    """Hallazgo de QA: si el evento ``close`` no llegaba, la página quedaba sin poder desplazarse."""
    assert "html:has(dialog[open])" in client.get("/static/css/app.css").get_data(as_text=True)
    assert "classList.add('overflow-hidden')" not in client.get("/static/js/reviews.js").get_data(as_text=True)


def test_the_scrollable_cookie_table_is_reachable_with_the_keyboard(client):
    """Hallazgo de axe-core (scrollable-region-focusable): una región con scroll debe poder enfocarse."""
    html = client.get("/politica-de-cookies").get_data(as_text=True)
    assert re.search(r'overflow-x-auto[^>]*role="region"[^>]*tabindex="0"', html)


def test_dialog_buttons_dispatch_through_an_action_table(client, mocked):
    """Abierto/cerrado: cada botón declara ``data-action`` y el JS tiene una tabla de acciones, no una cadena de if."""
    js = client.get("/static/js/reviews.js").get_data(as_text=True)
    assert "const ACTIONS = {" in js and "ACTIONS[trigger.dataset.action]" in js
    for action in ("close", "more", "'delete-mine'", "pin", "'moderate-delete'"):
        assert action in js
    mocked.get(f"{AUTH}/api/v1/auth/me", json=USER)
    login_cookies(client)
    assert 'data-action="close"' in client.get("/").get_data(as_text=True)
    for legacy in ("data-pin=", "data-more", "data-delete-mine", "data-moderate-delete", "data-reviews-close"):
        assert legacy not in js


def test_privacy_policy_discloses_the_security_log_ip_and_rate_limits(client):
    """La política debe decir lo que de verdad se guarda: IP, eventos de seguridad, plazo de retención y límites por IP."""
    html = client.get("/politica-de-privacidad").get_data(as_text=True)
    for expected in ("Registro de eventos de seguridad", "dirección IP", "a***@gmail.com", "180 días",
                     "límite de intentos por dirección IP", "consultar el registro de eventos de seguridad"):
        assert expected in html, expected
    assert "nunca contraseñas ni códigos" in html
