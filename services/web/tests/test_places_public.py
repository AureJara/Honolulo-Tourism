"""Lugares y fotos visibles para los visitantes (vía la pasarela web)."""

import requests

from conftest import CATALOG, login_cookies

KEY_FULL = "a" * 32 + "-1600.webp"


def test_places_api_is_public_and_proxied_with_short_cache(client, mocked):
    mocked.get(f"{CATALOG}/api/v1/places", json={"items": [{"name": "Cascada San Miguel"}], "total": 1})
    resp = client.get("/api/v1/places")                         # sin cookies de sesión
    assert resp.status_code == 200 and resp.get_json()["total"] == 1
    assert "Authorization" not in mocked.calls[0].request.headers
    assert resp.headers["Cache-Control"] == "public, max-age=30"


def test_place_detail_validates_the_slug_before_calling_the_catalog(client, mocked):
    mocked.get(f"{CATALOG}/api/v1/places/cascada-san-miguel", json={"name": "Cascada San Miguel"})
    assert client.get("/api/v1/places/cascada-san-miguel").status_code == 200
    n = len(mocked.calls)
    for bad in ("..%2f..%2fadmin", "A B", "x'%20OR%201=1", "a" * 81, "UPPER"):
        assert client.get(f"/api/v1/places/{bad}").status_code == 404, bad
    assert len(mocked.calls) == n


def test_places_api_when_catalog_is_down(client, mocked):
    mocked.get(f"{CATALOG}/api/v1/places", body=requests.ConnectionError("x"))
    resp = client.get("/api/v1/places")
    assert resp.status_code == 503 and "catálogo de lugares no está disponible" in resp.get_json()["detail"]


def test_media_is_proxied_with_immutable_caching_and_etag(client, mocked):
    mocked.get(f"{CATALOG}/media/{KEY_FULL}", body=b"RIFFxxxxWEBP", content_type="image/webp",
               headers={"ETag": '"abc"'})
    resp = client.get(f"/media/{KEY_FULL}")
    assert resp.status_code == 200 and resp.data == b"RIFFxxxxWEBP" and resp.mimetype == "image/webp"
    assert "immutable" in resp.headers["Cache-Control"] and resp.headers["ETag"] == '"abc"'
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_media_conditional_request_returns_304(client, mocked):
    mocked.get(f"{CATALOG}/media/{KEY_FULL}", status=304, headers={"ETag": '"abc"'})
    resp = client.get(f"/media/{KEY_FULL}", headers={"If-None-Match": '"abc"'})
    assert resp.status_code == 304
    assert mocked.calls[0].request.headers["If-None-Match"] == '"abc"'


def test_media_only_accepts_well_formed_keys_and_never_reaches_the_catalog_otherwise(client, mocked):
    for bad in ("..%2f..%2fetc%2fpasswd", "x.webp", "a" * 32 + "-9999.webp", "a" * 32 + "-1600.svg",
                "A" * 32 + "-1600.webp", "a" * 33 + "-1600.webp"):
        assert client.get(f"/media/{bad}").status_code == 404, bad
    assert len(mocked.calls) == 0


def test_media_unknown_file_and_service_down(client, mocked):
    mocked.get(f"{CATALOG}/media/{KEY_FULL}", status=404)
    assert client.get(f"/media/{KEY_FULL}").status_code == 404
    mocked.reset()
    mocked.get(f"{CATALOG}/media/{KEY_FULL}", body=requests.ConnectionError("x"))
    assert client.get(f"/media/{KEY_FULL}").status_code == 503


def test_main_page_contains_the_places_section(client, mocked):
    from conftest import AUTH, USER
    mocked.get(f"{AUTH}/api/v1/auth/me", json=USER)
    login_cookies(client)
    html = client.get("/").get_data(as_text=True)
    assert 'id="lugares"' in html and "Nuestras Majestuosas Cascadas" in html and 'id="places-grid"' in html
