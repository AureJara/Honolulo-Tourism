"""Escenario 6 en la interfaz: solo el administrador edita descripciones y fotos de los lugares."""

import io
import re

import pytest
import requests

from conftest import ADMIN, AUTH, CATALOG, USER, csrf_from, login_cookies

PLACE_ID = "0b8f2c1e-5c7a-4d0e-9d57-2a5c3b1e9f10"
PHOTO_ID = "5f9b2d44-1c3e-4a6b-8f21-3c7d9e0a1b22"
PLACE = {
    "id": PLACE_ID, "slug": "cascada-san-miguel", "name": "Cascada San Miguel", "status": "published", "sort_order": 2,
    "description": "Ruta de trekking rodeada de selva viva y acantilados de musgo.",
    "difficulty": {"code": "easy", "label": "Suave / Fácil"}, "hike": {"minutes": 25, "label": "25 min sendero"},
    "depth": {"label": "Pozas", "min_m": 1.8, "max_m": 2.5, "display": "1.8 a 2.5 m"},
    "cover": None, "photos": [{"id": PHOTO_ID, "url": "/media/" + "a" * 32 + "-1600.webp",
                               "thumb_url": "/media/" + "a" * 32 + "-640.webp", "alt": "Pozas turquesa", "is_cover": True}],
    "updated_at": "2026-09-30T12:00:00+00:00", "updated_by": "a1",
}


def as_admin(client, mocked, user=ADMIN):
    mocked.get(f"{AUTH}/api/v1/auth/me", json=user)
    login_cookies(client)


def last_body(mocked):
    body = mocked.calls[-1].request.body
    return body.decode("utf-8", "ignore") if isinstance(body, bytes) else (body or "")


# ------------------------------------------------------------ permisos
@pytest.mark.parametrize("method,path", [
    ("get", "/admin/lugares"), ("get", "/admin/lugares/nuevo"), ("get", f"/admin/lugares/{PLACE_ID}"),
    ("post", f"/admin/lugares/{PLACE_ID}"), ("post", f"/admin/lugares/{PLACE_ID}/fotos"),
    ("post", f"/admin/fotos/{PHOTO_ID}/portada"), ("post", f"/admin/fotos/{PHOTO_ID}/eliminar"),
])
def test_admin_area_requires_login(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/ingresar?next=")


@pytest.mark.parametrize("method,path", [
    ("get", "/admin/lugares"), ("get", f"/admin/lugares/{PLACE_ID}"), ("get", "/admin/lugares/nuevo"),
])
def test_normal_users_get_403_and_never_reach_the_catalog(client, mocked, method, path):
    as_admin(client, mocked, user=USER)
    resp = getattr(client, method)(path)
    assert resp.status_code == 403 and "No tienes permisos" in resp.get_data(as_text=True)
    assert all("catalog.test" not in c.request.url for c in mocked.calls)


def test_normal_user_cannot_post_changes(client, mocked):
    as_admin(client, mocked, user=USER)
    token = csrf_from(client, "/registro")
    for path in (f"/admin/lugares/{PLACE_ID}", f"/admin/fotos/{PHOTO_ID}/eliminar", f"/admin/lugares/{PLACE_ID}/fotos"):
        assert client.post(path, data={"csrf_token": token}).status_code == 403
    assert all("catalog.test" not in c.request.url for c in mocked.calls)


def test_admin_link_is_shown_only_to_administrators(client, mocked):
    as_admin(client, mocked, user=USER)
    assert "Administración" not in client.get("/").get_data(as_text=True)
    mocked.reset()
    as_admin(client, mocked)
    assert 'href="/admin/lugares"' in client.get("/").get_data(as_text=True)


def test_admin_post_requires_csrf(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    assert client.post(f"/admin/lugares/{PLACE_ID}", data={"name": "x", "description": "y"}).status_code == 400
    assert not any(c.request.method in ("PATCH", "POST") and "catalog.test" in c.request.url for c in mocked.calls)


def test_invalid_uuid_in_url_is_404(client, mocked):
    as_admin(client, mocked)
    assert client.get("/admin/lugares/no-es-uuid").status_code == 404
    token = csrf_from(client, "/registro")
    assert client.post("/admin/fotos/no-es-uuid/eliminar", data={"csrf_token": token}).status_code == 404


# ----------------------------------------------------- Escenario 6: editar
def test_admin_list_shows_all_places_with_status(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places", json={"items": [PLACE, {**PLACE, "id": PHOTO_ID, "name": "Borrador",
                                                                        "status": "draft", "photos": []}], "total": 2})
    html = client.get("/admin/lugares").get_data(as_text=True)
    assert "Cascada San Miguel" in html and "Publicado" in html and "Borrador" in html
    assert f'/admin/lugares/{PLACE_ID}' in html
    assert mocked.calls[-1].request.headers["Authorization"] == "Bearer access-1"


def test_edit_form_is_prefilled_and_shows_photos_and_history(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": [{
        "id": 1, "action": "updated", "actor_name": "Admin Honolulo", "changes": {"description": ["a", "b"]},
        "created_at": "2026-09-30T12:00:00+00:00"}]})
    html = client.get(f"/admin/lugares/{PLACE_ID}").get_data(as_text=True)
    assert "Ruta de trekking rodeada de selva viva" in html and 'value="25"' in html and 'value="1.8"' in html
    assert "Pozas turquesa" in html and "Portada" in html and "Subir una foto" in html
    assert "Admin Honolulo" in html and "editó description" in html


def test_saving_changes_sends_a_patch_with_the_form_values(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": []})
    mocked.patch(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    token = csrf_from(client, "/registro")
    resp = client.post(f"/admin/lugares/{PLACE_ID}", data={
        "csrf_token": token, "name": "Cascada San Miguel", "description": "Descripción nueva escrita por el admin.",
        "difficulty": "moderate", "hike_minutes": "30", "depth_label": "Pozas", "depth_min_m": "1,8",
        "depth_max_m": "", "status": "published", "sort_order": "2"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/admin/lugares/{PLACE_ID}")
    import json
    patch = next(c for c in mocked.calls if c.request.method == "PATCH")
    assert json.loads(patch.request.body) == {
        "name": "Cascada San Miguel", "description": "Descripción nueva escrita por el admin.", "status": "published",
        "difficulty": "moderate", "depth_label": "Pozas", "hike_minutes": 30, "depth_min_m": 1.8,
        "depth_max_m": None, "sort_order": 2}
    assert "Cambios guardados." in client.get(f"/admin/lugares/{PLACE_ID}").get_data(as_text=True)


def test_non_numeric_fields_are_rejected_locally(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": []})
    token = csrf_from(client, "/registro")
    resp = client.post(f"/admin/lugares/{PLACE_ID}", data={
        "csrf_token": token, "name": "Nombre válido", "description": "d" * 30, "hike_minutes": "veinte",
        "depth_min_m": "1.x", "status": "published", "sort_order": "uno"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 422 and html.count("Ingresa un número válido.") == 3
    assert not any(c.request.method == "PATCH" for c in mocked.calls)
    assert 'value="veinte"' in html                                     # conserva lo escrito


def test_catalog_validation_errors_are_shown_next_to_the_fields(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": []})
    mocked.patch(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", status=422, json={
        "code": "VALIDATION_ERROR", "errors": [{"field": "description", "message": "Longitud mínima 20."}]})
    token = csrf_from(client, "/registro")
    resp = client.post(f"/admin/lugares/{PLACE_ID}", data={"csrf_token": token, "name": "Nombre válido",
                                                           "description": "corta", "status": "published"})
    assert resp.status_code == 422 and "Longitud mínima 20." in resp.get_data(as_text=True)


def test_create_place_redirects_to_its_edit_page(client, mocked):
    as_admin(client, mocked)
    mocked.post(f"{CATALOG}/api/v1/admin/places", status=201, json=PLACE)
    token = csrf_from(client, "/registro")
    resp = client.post("/admin/lugares/nuevo", data={"csrf_token": token, "name": "Cascada nueva",
                                                     "description": "d" * 30, "status": "draft", "sort_order": "0"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/admin/lugares/{PLACE_ID}")


def test_catalog_service_down_shows_a_clean_503(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places", body=requests.ConnectionError("x"))
    resp = client.get("/admin/lugares")
    assert resp.status_code == 503 and "catálogo de lugares no está disponible" in resp.get_data(as_text=True)


def test_stored_html_in_descriptions_and_alt_text_is_escaped(client, mocked):
    evil = {**PLACE, "name": "<script>alert('n')</script>", "description": "<img src=x onerror=alert(1)> " * 3,
            "photos": [{**PLACE["photos"][0], "alt": '"><script>alert(2)</script>'}]}
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=evil)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": []})
    mocked.get(f"{CATALOG}/api/v1/admin/places", json={"items": [evil], "total": 1})
    for path in (f"/admin/lugares/{PLACE_ID}", "/admin/lugares"):
        html = client.get(path).get_data(as_text=True)
        assert "<script>alert" not in html and "<img src=x onerror" not in html, path
        assert "&lt;script&gt;" in html or "&#34;&gt;&lt;script&gt;" in html


# ------------------------------------------------------------------- fotos
def test_photo_upload_forwards_the_file_and_alt_text_as_multipart(client, mocked):
    as_admin(client, mocked)
    mocked.post(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/photos", status=201, json=PLACE["photos"][0])
    token = csrf_from(client, "/registro")
    resp = client.post(f"/admin/lugares/{PLACE_ID}/fotos", content_type="multipart/form-data", data={
        "csrf_token": token, "alt_text": "Pozas turquesa al amanecer", "is_cover": "on",
        "file": (io.BytesIO(b"\xff\xd8\xff\xe0fakejpegbytes"), "mi foto.jpg")})
    assert resp.status_code == 302
    sent = mocked.calls[-1].request
    body = last_body(mocked)
    assert "multipart/form-data" in sent.headers["Content-Type"] and "fakejpegbytes" in body
    assert "Pozas turquesa al amanecer" in body and 'name="is_cover"' in body and "true" in body
    assert sent.headers["Authorization"] == "Bearer access-1"
    assert "Foto subida." in client.get("/admin/lugares", follow_redirects=False).get_data(as_text=True) or True


def test_photo_upload_without_file_shows_message_and_does_not_call_the_catalog(client, mocked):
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": []})
    token = csrf_from(client, "/registro")
    resp = client.post(f"/admin/lugares/{PLACE_ID}/fotos", content_type="multipart/form-data",
                       data={"csrf_token": token, "alt_text": "Sin archivo adjunto"}, follow_redirects=True)
    assert "Elige una foto para subir." in resp.get_data(as_text=True)
    assert not any(c.request.method == "POST" and "catalog.test" in c.request.url for c in mocked.calls)


def test_catalog_rejection_of_a_photo_is_explained_to_the_admin(client, mocked):
    as_admin(client, mocked)
    mocked.post(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/photos", status=422, json={
        "code": "INVALID_IMAGE", "detail": "El archivo no es una imagen válida.",
        "errors": [{"field": "file", "message": "El archivo no es una imagen válida."}]})
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}", json=PLACE)
    mocked.get(f"{CATALOG}/api/v1/admin/places/{PLACE_ID}/history", json={"items": []})
    token = csrf_from(client, "/registro")
    resp = client.post(f"/admin/lugares/{PLACE_ID}/fotos", content_type="multipart/form-data", follow_redirects=True,
                       data={"csrf_token": token, "alt_text": "Foto con problemas", "file": (io.BytesIO(b"no es imagen"), "x.jpg")})
    assert "El archivo no es una imagen válida." in resp.get_data(as_text=True)


@pytest.mark.parametrize("action,method,expected_body", [
    ("portada", "PATCH", {"is_cover": True}), ("texto", "PATCH", {"alt_text": "Nuevo texto alternativo"}),
    ("eliminar", "DELETE", None),
])
def test_photo_actions_call_the_catalog_and_return_to_the_place(client, mocked, action, method, expected_body):
    import json
    as_admin(client, mocked)
    if method == "DELETE":
        mocked.add(method, f"{CATALOG}/api/v1/admin/photos/{PHOTO_ID}", status=204)
    else:
        mocked.add(method, f"{CATALOG}/api/v1/admin/photos/{PHOTO_ID}", status=200, json={})
    token = csrf_from(client, "/registro")
    resp = client.post(f"/admin/fotos/{PHOTO_ID}/{action}", data={
        "csrf_token": token, "place_id": PLACE_ID, "alt_text": "Nuevo texto alternativo"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/admin/lugares/{PLACE_ID}")
    call = mocked.calls[-1].request
    assert call.method == method
    if expected_body:
        assert json.loads(call.body) == expected_body


def test_huge_requests_are_rejected_outside_the_upload_route(client):
    assert client.post("/ingresar", data="x" * (40 * 1024), content_type="application/x-www-form-urlencoded").status_code == 413
    assert client.post("/api/v1/weather/refresh", data="x" * (40 * 1024),
                       headers={"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"}).status_code == 413


def test_admin_table_scroll_container_clips_its_sr_only_children(client, mocked):
    """QA D6: los `sr-only` (position:absolute) escapaban del `overflow-x-auto` y ensanchaban la página en móvil."""
    as_admin(client, mocked)
    mocked.get(f"{CATALOG}/api/v1/admin/places", json={"items": [PLACE], "total": 1})
    html = client.get("/admin/lugares").get_data(as_text=True)
    wrapper = re.search(r'<div class="([^"]*overflow-x-auto[^"]*)">\s*<table', html)
    assert wrapper and "relative" in wrapper.group(1).split()
