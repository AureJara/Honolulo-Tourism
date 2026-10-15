"""Escenario 9: quien no está registrado puede ver la información, pero no puntuar ni comentar."""

from pathlib import Path

import pytest

from conftest import AUTH, CATALOG, USER, login_cookies

SLUG = "catarata-gloriapata"
XHR = {"X-Requested-With": "XMLHttpRequest"}


def home(client) -> str:
    return client.get("/").get_data(as_text=True)


def test_a_visitor_sees_the_places_with_their_photos_and_reviews_entry_point(client):
    html = home(client)
    assert 'id="lugares"' in html and 'id="places-grid"' in html and "Nuestras Majestuosas Cascadas" in html
    assert 'data-auth="0"' in html and 'data-role="visitor"' in html
    assert "reviews-dialog" in html and "reviews.js" in html


@pytest.mark.parametrize("hidden", ["Actualizar clima", 'id="current-card"', 'id="calendario"', 'id="day-panel"',
                                    'id="historial"', "Historial meteorológico", "Salir",
                                    'id="afluencia"', "visits.js", "Afluencia de visitantes"])
def test_a_visitor_does_not_get_the_weather_module(client, hidden):
    assert hidden not in home(client)


def test_a_visitor_is_invited_to_log_in_or_register(client):
    html = home(client)
    assert 'id="visitor-card"' in html and 'href="/ingresar"' in html and 'href="/registro"' in html
    assert "Crear cuenta" in html


def test_a_member_still_gets_the_weather_module_and_no_visitor_card(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", json=USER)
    login_cookies(client)
    html = home(client)
    assert 'data-auth="1"' in html and 'id="current-card"' in html and 'id="calendario"' in html
    assert "Actualizar clima" in html and "Salir" in html
    assert 'id="visitor-card"' not in html and 'data-role="user"' in html


@pytest.mark.parametrize("path", ["/api/v1/weather/current", "/api/v1/weather/forecast?date=2026-10-01",
                                  "/api/v1/weather/calendar?month=2026-10", "/api/v1/weather/history",
                                  "/api/v1/weather/evaluation/1"])
def test_the_weather_api_stays_protected_for_visitors(client, mocked, path):
    resp = client.get(path)
    assert resp.status_code == 401 and resp.get_json()["code"] == "AUTH_REQUIRED"
    assert len(mocked.calls) == 0


def test_visitors_can_read_the_reviews(client, mocked):
    mocked.get(f"{CATALOG}/api/v1/places/{SLUG}/reviews", json={"items": [], "total": 0, "summary": None})
    assert client.get(f"/api/v1/places/{SLUG}/reviews").status_code == 200
    assert "Authorization" not in mocked.calls[0].request.headers


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_visitors_cannot_rate_or_comment(client, mocked, method):
    resp = client.open(f"/api/v1/places/{SLUG}/reviews/mine", method=method, headers=XHR,
                       json={"rating": 5, "comment": "Hermosa"} if method == "PUT" else None)
    assert resp.status_code == 401 and resp.get_json()["code"] == "AUTH_REQUIRED"
    assert len(mocked.calls) == 0                    # ni siquiera llega al catálogo


def test_the_scripts_do_not_ask_a_visitor_for_session_data(app):
    """Sin esto el visitante sería enviado a /ingresar al abrir una cascada (la API de «mi opinión» responde 401)."""
    js = Path(app.static_folder) / "js"
    app_js, reviews_js = (js / "app.js").read_text(encoding="utf-8"), (js / "reviews.js").read_text(encoding="utf-8")
    assert "if (!authed) { loadPlaces(); return; }" in app_js and "plural, authed }" in app_js
    assert "reset && authed ? api(" in reviews_js and "if (!authed) return loginPromptHTML();" in reviews_js
    assert "Inicia sesión para opinar" in reviews_js


def test_the_place_photos_open_in_a_viewer_that_shows_the_whole_picture(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="photo-dialog"' in html and 'id="photo-img"' in html and "object-contain" in html
    app_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "data-open-photo" in app_js and "bindPhotoViewer();" in app_js
