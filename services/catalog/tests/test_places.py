"""Escenario 6: el administrador puede modificar la información (descripciones) de los lugares."""

import uuid

import pytest

from conftest import first_place_id, token

PUBLIC = "/api/v1/places"
ADMIN = "/api/v1/admin/places"


# -------------------------------------------------------------- lectura pública
def test_public_list_is_empty_before_seeding(client):
    assert client.get(PUBLIC).get_json() == {"items": [], "total": 0}


def test_seed_loads_the_four_design_places_in_order(seeded):
    body = seeded.get(PUBLIC).get_json()
    assert [p["name"] for p in body["items"]] == [
        "Catarata Velo de las Ninfas", "Cascada San Miguel", "Catarata Santa Carmen", "Catarata Gloriapata"]
    velo, san_miguel, _, gloria = body["items"]
    assert velo["difficulty"] == {"code": "moderate", "label": "Moderada"}
    assert velo["hike"] == {"minutes": 45, "label": "45 min sendero"}
    assert velo["depth"] == {"label": "Piscina Natural", "min_m": None, "max_m": 3.5, "display": "Hasta 3.5 m"}
    assert san_miguel["difficulty"]["label"] == "Suave / Fácil" and san_miguel["depth"]["display"] == "1.8 a 2.5 m"
    assert gloria["depth"]["display"] == "Hasta 3.0 m"
    assert "toboganes de piedra natural" in velo["description"]
    assert velo["cover"] is None and velo["photos"] == []


def test_seed_is_idempotent(app, seeded):
    from app.seed import seed_places
    with app.app_context():
        assert seed_places() == 0
    assert seeded.get(PUBLIC).get_json()["total"] == 4


def test_public_endpoints_need_no_login_and_are_cacheable(seeded):
    resp = seeded.get(PUBLIC)
    assert resp.status_code == 200 and "max-age=60" in resp.headers["Cache-Control"]
    assert seeded.get(f"{PUBLIC}/cascada-san-miguel").get_json()["name"] == "Cascada San Miguel"


def test_unknown_slug_is_404_and_injection_is_harmless(seeded):
    assert seeded.get(f"{PUBLIC}/no-existe").status_code == 404
    assert seeded.get(f"{PUBLIC}/x'%20OR%20'1'='1").status_code == 404
    assert seeded.get(f"{PUBLIC}/x';DROP TABLE places;--").status_code == 404
    assert seeded.get(PUBLIC).get_json()["total"] == 4


# ------------------------------------------------------------- permisos por rol
ADMIN_ENDPOINTS = [
    ("get", ADMIN), ("post", ADMIN), ("get", f"{ADMIN}/{uuid.uuid4()}"), ("patch", f"{ADMIN}/{uuid.uuid4()}"),
    ("get", f"{ADMIN}/{uuid.uuid4()}/history"), ("post", f"{ADMIN}/{uuid.uuid4()}/photos"),
    ("patch", f"/api/v1/admin/photos/{uuid.uuid4()}"), ("delete", f"/api/v1/admin/photos/{uuid.uuid4()}"),
]


@pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
def test_admin_endpoints_reject_anonymous_and_normal_users(client, user, method, path):
    anonymous = getattr(client, method)(path)
    assert anonymous.status_code == 401
    normal = getattr(client, method)(path, headers=user, json={})
    assert normal.status_code == 403 and normal.get_json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("bad_token", [
    token("admin", type="refresh"),                                   # refresh no sirve
    token("admin", aud="otra-api"), token("admin", iss="otro-emisor"),
    token("admin", exp=1),                                            # vencido
])
def test_admin_rejects_invalid_tokens(client, bad_token):
    assert client.get(ADMIN, headers={"Authorization": f"Bearer {bad_token}"}).status_code == 401


def test_forged_admin_token_with_wrong_secret_is_rejected(client):
    import jwt
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    forged = jwt.encode({"sub": "x", "role": "admin", "type": "access", "iss": "honolulo-auth", "aud": "honolulo-api",
                         "iat": now, "exp": now + timedelta(minutes=5)}, "otro-secreto-otro-secreto-12345678", "HS256")
    assert client.get(ADMIN, headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_alg_none_token_is_rejected(client):
    import base64, json
    b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    none_token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': 'x', 'role': 'admin', 'type': 'access'})}."
    assert client.get(ADMIN, headers={"Authorization": f"Bearer {none_token}"}).status_code == 401


# ------------------------------------------------ Escenario 6: editar descripciones
def test_admin_edits_a_description_and_visitors_see_it(seeded, admin):
    place_id = first_place_id(seeded, admin)
    new_text = "Nueva descripción redactada por el administrador: aguas turquesa y sendero señalizado."
    resp = seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={"description": new_text})
    assert resp.status_code == 200 and resp.get_json()["description"] == new_text
    public = seeded.get(PUBLIC).get_json()["items"][0]
    assert public["description"] == new_text and public["name"] == "Catarata Velo de las Ninfas"


def test_edit_is_audited_with_who_what_and_previous_value(seeded, admin):
    place_id = first_place_id(seeded, admin)
    old = seeded.get(f"{ADMIN}/{place_id}", headers=admin).get_json()["description"]
    new_text = "Texto actualizado con más de veinte caracteres para el lugar."
    seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={"description": new_text, "hike_minutes": 50})
    entry = seeded.get(f"{ADMIN}/{place_id}/history", headers=admin).get_json()["items"][0]
    assert entry["action"] == "updated" and entry["actor_name"] == "Admin Honolulo" and entry["actor_id"] == "admin-1"
    assert entry["changes"]["description"] == [old, new_text]
    assert entry["changes"]["hike_minutes"] == [45, 50]


def test_no_op_patch_does_not_create_audit_entries(seeded, admin):
    place_id = first_place_id(seeded, admin)
    current = seeded.get(f"{ADMIN}/{place_id}", headers=admin).get_json()
    seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={"description": current["description"]})
    assert seeded.get(f"{ADMIN}/{place_id}/history", headers=admin).get_json()["items"] == []


def test_admin_can_unpublish_and_the_place_disappears_from_the_public_list(seeded, admin):
    place_id = first_place_id(seeded, admin)
    seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={"status": "draft"})
    names = [p["name"] for p in seeded.get(PUBLIC).get_json()["items"]]
    assert "Catarata Velo de las Ninfas" not in names and len(names) == 3
    assert seeded.get(f"{PUBLIC}/catarata-velo-de-las-ninfas").status_code == 404
    assert seeded.get(ADMIN, headers=admin).get_json()["total"] == 4       # el admin sigue viéndolo


@pytest.mark.parametrize("payload,field", [
    ({"description": "corta"}, "description"),
    ({"description": "x" * 2001}, "description"),
    ({"description": " " * 40}, "description"),
    ({"name": "ab"}, "name"),
    ({"name": "n" * 121}, "name"),
    ({"difficulty": "imposible"}, "difficulty"),
    ({"hike_minutes": 0}, "hike_minutes"),
    ({"hike_minutes": 601}, "hike_minutes"),
    ({"hike_minutes": 4.5}, "hike_minutes"),
    ({"hike_minutes": "45"}, "hike_minutes"),
    ({"status": "borrado"}, "status"),
    ({"sort_order": -1}, "sort_order"),
    ({"depth_min_m": 5, "depth_max_m": 2}, "depth_min_m"),
    ({"depth_max_m": -1}, "depth_max_m"),
])
def test_edit_validation(seeded, admin, payload, field):
    place_id = first_place_id(seeded, admin)
    resp = seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json=payload)
    assert resp.status_code == 422, payload
    assert field in [e["field"] for e in resp.get_json()["errors"]]


def test_inconsistent_depth_against_stored_value_is_rejected(seeded, admin):
    place_id = first_place_id(seeded, admin)                       # max = 3.5
    resp = seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={"depth_min_m": 9})
    assert resp.status_code == 422
    assert seeded.get(f"{ADMIN}/{place_id}", headers=admin).get_json()["depth"]["min_m"] is None


def test_empty_or_non_object_body_is_rejected(seeded, admin):
    place_id = first_place_id(seeded, admin)
    assert seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={}).status_code == 422
    assert seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={"desconocido": 1}).status_code == 422
    assert seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json=[1]).status_code == 400
    assert seeded.patch(f"{ADMIN}/{place_id}", headers=admin, data="x").status_code == 400


def test_control_characters_are_stripped_and_newlines_kept(seeded, admin):
    place_id = first_place_id(seeded, admin)
    text = "Primera línea del texto.\nSegunda línea\x00\x07 con caracteres de control."
    body = seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={"description": text}).get_json()
    assert body["description"] == "Primera línea del texto.\nSegunda línea con caracteres de control."


def test_html_is_stored_verbatim_as_data(seeded, admin):
    """El texto es dato: se escapa al mostrarlo (ver pruebas de la web), no se altera en el servicio."""
    place_id = first_place_id(seeded, admin)
    payload = "<script>alert(1)</script> descripción con etiquetas para probar el escape."
    body = seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={"description": payload}).get_json()
    assert body["description"] == payload


def test_unknown_place_is_404_and_bad_uuid_does_not_crash(seeded, admin):
    assert seeded.patch(f"{ADMIN}/{uuid.uuid4()}", headers=admin, json={"name": "Nombre válido"}).status_code == 404
    assert seeded.get(f"{ADMIN}/no-es-uuid", headers=admin).status_code == 404


def test_create_place_generates_unique_ascii_slugs_and_starts_as_draft(client, admin):
    payload = {"name": "Cascada Ñandú Ímpetu", "description": "Descripción larga de la nueva cascada del santuario."}
    first = client.post(ADMIN, headers=admin, json=payload)
    second = client.post(ADMIN, headers=admin, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.get_json()["slug"] == "cascada-nandu-impetu" and second.get_json()["slug"] == "cascada-nandu-impetu-2"
    assert first.get_json()["status"] == "draft"
    assert client.get(PUBLIC).get_json()["total"] == 0           # un borrador no es público


def test_create_requires_name_and_description(client, admin):
    assert client.post(ADMIN, headers=admin, json={"name": "Solo nombre"}).status_code == 422
    assert client.post(ADMIN, headers=admin, json={"description": "Solo descripción sin nombre alguno."}).status_code == 422


def test_admin_list_includes_drafts_and_metadata(seeded, admin):
    place_id = first_place_id(seeded, admin)
    seeded.patch(f"{ADMIN}/{place_id}", headers=admin, json={"status": "archived"})
    item = seeded.get(ADMIN, headers=admin).get_json()["items"][0]
    assert item["status"] == "archived" and item["updated_by"] == "admin-1"
