"""Criterios de aceptación del spec (Escenarios 1–8) ejecutados contra el sistema real."""

import re
import time

import pytest
import requests

from conftest import (AUTH_URL, BASE, CATALOG_URL, PASSWORD, Web, code_from_mail, jpeg, latest_mail, mail_body,
                      mail_count, parse_mail, unique_email)


# ====================================================== RF01 · Autenticación
def test_rf01_anonymous_users_cannot_reach_the_weather_module(web):
    page = web.get("/")
    assert page.status_code == 302 and page.headers["Location"].startswith("/ingresar")
    for path in ("/api/v1/weather/current", "/api/v1/weather/forecast?date=2026-10-01",
                 "/api/v1/weather/calendar?month=2026-10", "/api/v1/weather/evaluation/1",
                 "/api/v1/weather/history", "/api/v1/weather/location"):
        resp = web.api("GET", path)
        assert resp.status_code == 401 and resp.json()["code"] == "AUTH_REQUIRED", path


# ================================================== Escenario 1 · clima actual
def test_scenario1_current_weather_shows_all_required_fields(member_web):
    body = member_web.api("GET", "/api/v1/weather/current").json()
    assert body["location"] == "Honolulo"
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d-05:00$", body["updated_at"]), body["updated_at"]   # RN04
    w = body["weather"]
    for field in ("temperature", "feels_like", "humidity", "precipitation", "rain_probability",
                  "wind_speed", "cloud_cover", "condition"):
        assert field in w, field
    assert isinstance(w["temperature"], (int, float)) and -10 < w["temperature"] < 50
    assert 0 <= w["humidity"] <= 100 and w["wind_speed"] >= 0 and w["precipitation"] >= 0
    assert body["stale"] is False


def test_scenario1_main_page_renders_the_weather_card(member_web):
    html = member_web.get("/").text
    for expected in ("Honolulo", "Monitoreo meteorológico", "Actualizar clima", "Planifica tu Visita",
                     "Mariano Dámaso Beraún", "Leoncio Prado", "Huánuco", "Perú"):
        assert expected in html, expected


# ===================================================== RF02 · Ubicación (RN01)
def test_rf02_location_is_honolulo_with_configured_coordinates(member_web):
    loc = member_web.api("GET", "/api/v1/weather/location").json()
    assert (loc["name"], loc["district"], loc["province"], loc["department"], loc["country"]) == (
        "Honolulo", "Mariano Dámaso Beraún", "Leoncio Prado", "Huánuco", "Perú")
    assert loc["timezone"] == "America/Lima" and -10 < loc["latitude"] < -9 and -77 < loc["longitude"] < -75


# ============================================== Escenario 2 · selección de fecha
def test_scenario2_calendar_and_day_forecast(member_web):
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 5 * 3600))
    month = today[:7]
    cal = member_web.api("GET", f"/api/v1/weather/calendar?month={month}").json()
    assert cal["location"] == "Honolulo" and cal["timezone"] == "America/Lima"
    available = [d for d in cal["days"] if d["available"]]
    assert available, "el calendario no trae ningún día con pronóstico"
    day = next((d for d in available if d["date"] >= today), available[-1])
    assert {"temperature", "condition", "icon"} <= set(day)

    forecast = member_web.api("GET", f"/api/v1/weather/forecast?date={day['date']}").json()
    assert forecast["location"] == "Honolulo" and forecast["date"] == day["date"] and forecast["available"] is True
    entry = forecast["forecast"][0]
    for field in ("time", "temperature", "humidity", "rain_probability", "precipitation", "condition", "wind_speed"):
        assert field in entry, field
    assert re.match(r"\d\d:00$", entry["time"]) and isinstance(entry["forecast_id"], int)
    assert forecast["bands"] and {"from", "to", "temperature", "label"} <= set(forecast["bands"][0])
    assert all(m in forecast["metrics"] for m in ("rain_probability", "humidity", "wind_speed", "precipitation"))


def test_rf04_calendar_days_come_only_from_stored_forecasts(member_web):
    """RN03: fuera del horizonte del proveedor no se inventan datos."""
    far = member_web.api("GET", "/api/v1/weather/calendar?month=2028-02").json()
    assert len(far["days"]) == 29 and not any(d["available"] for d in far["days"])      # 2028 es bisiesto
    day = member_web.api("GET", "/api/v1/weather/forecast?date=2028-02-10").json()
    assert day["available"] is False and day["forecast"] == [] and "Aún no hay pronóstico" in day["message"]


# ============================================= Escenarios 3 y 4 · evaluación
def test_scenario4_future_forecast_is_pending_with_the_exact_message(member_web):
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 5 * 3600))
    year, month = int(today[:4]), int(today[5:7])
    months = [f"{year}-{month:02d}", f"{year + (month == 12)}-{(month % 12) + 1:02d}"]      # este mes y el siguiente
    days = [d for m in months for d in member_web.api("GET", f"/api/v1/weather/calendar?month={m}").json()["days"]]
    future = next(d["date"] for d in days if d["available"] and d["date"] > today)
    entry = member_web.api("GET", f"/api/v1/weather/forecast?date={future}").json()["forecast"][0]
    body = member_web.api("GET", f"/api/v1/weather/evaluation/{entry['forecast_id']}").json()
    assert body["status"] == "pending" and body["accuracy_score"] is None
    assert body["message"] == "Pronóstico pendiente de evaluación."
    assert "variables" not in body                                          # RN07: no se inventa una puntuación


def test_scenario3_evaluation_unknown_forecast_is_404(member_web):
    assert member_web.api("GET", "/api/v1/weather/evaluation/99999999").status_code == 404


# ============================== Escenario 6 · cuenta de administrador (lugares)
def test_scenario6_public_can_see_the_places_from_the_design():
    places = requests.get(f"{BASE}/api/v1/places", timeout=15).json()
    names = [p["name"] for p in places["items"]]
    assert "Catarata Velo de las Ninfas" in names and "Cascada San Miguel" in names


def test_scenario6_normal_user_cannot_administer_places(member_web, member_token):
    assert member_web.get("/admin/lugares").status_code == 403
    resp = requests.get(f"{CATALOG_URL}/api/v1/admin/places", headers={"Authorization": f"Bearer {member_token}"}, timeout=15)
    assert resp.status_code == 403


def test_scenario6_admin_edits_description_and_uploads_a_photo_visitors_see_it(admin_web, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    items = requests.get(f"{CATALOG_URL}/api/v1/admin/places", headers=headers, timeout=15).json()["items"]
    place = next(p for p in items if p["slug"] == "cascada-san-miguel")
    original = place["description"]
    marker = f"QA-{int(time.time())}"
    new_desc = f"{original.split(' [QA')[0]} [QA {marker}]"
    try:
        # 1) editar la descripción desde la interfaz de administración (formulario real con CSRF)
        page = admin_web.get(f"/admin/lugares/{place['id']}")
        assert page.status_code == 200 and "Subir una foto" in page.text
        resp = admin_web.form(f"/admin/lugares/{place['id']}", {
            "name": place["name"], "description": new_desc, "difficulty": "easy", "hike_minutes": "25",
            "depth_label": "Pozas", "depth_min_m": "1.8", "depth_max_m": "2.5", "status": "published",
            "sort_order": "2"}, csrf_path=f"/admin/lugares/{place['id']}")
        assert resp.status_code == 302
        public = requests.get(f"{BASE}/api/v1/places/cascada-san-miguel", timeout=15).json()
        assert marker in public["description"]

        # 2) subir una foto y dejarla como portada
        up = admin_web.form(f"/admin/lugares/{place['id']}/fotos", {"alt_text": f"Foto de prueba {marker}", "is_cover": "on"},
                            csrf_path=f"/admin/lugares/{place['id']}", files={"file": ("qa.jpg", jpeg(), "image/jpeg")})
        assert up.status_code == 302
        public = requests.get(f"{BASE}/api/v1/places/cascada-san-miguel", timeout=15).json()
        assert public["cover"] and marker in public["cover"]["alt"]
        media = requests.get(BASE + public["cover"]["url"], timeout=15)
        assert media.status_code == 200 and media.headers["Content-Type"] == "image/webp"
        assert "immutable" in media.headers["Cache-Control"]
    finally:
        # limpieza: restaurar la descripción y borrar las fotos de la prueba
        requests.patch(f"{CATALOG_URL}/api/v1/admin/places/{place['id']}", headers=headers,
                       json={"description": original}, timeout=15)
        current = requests.get(f"{CATALOG_URL}/api/v1/admin/places/{place['id']}", headers=headers, timeout=15).json()
        for photo in current["photos"]:
            if marker in photo["alt"]:
                requests.delete(f"{CATALOG_URL}/api/v1/admin/photos/{photo['id']}", headers=headers, timeout=15)


# ============ Escenarios 7 y 8 · registro con datos reales, código y consentimiento
def test_scenario7_full_registration_with_email_code_and_consent():
    w, email = Web(), unique_email(domain="gmail.com")
    resp = w.register(email, first="María José", last="Núñez Quispe")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/verificar")
    assert not w.s.cookies.get("hn_access")                                # sin sesión hasta confirmar

    mail = latest_mail(email)
    msg = parse_mail(mail)
    assert msg["To"] == email and msg["Subject"].startswith("Tu código de confirmación de Honolulo")
    body = mail_body(mail)
    assert "Hola María José" in body and "Vence en 10 minutos" in body and "ignora este mensaje" in body

    # no se puede entrar antes de confirmar el correo
    w2 = Web()
    blocked = w2.login(email)
    assert blocked.status_code == 302 and blocked.headers["Location"].endswith("/verificar")
    assert not w2.s.cookies.get("hn_access")

    # el código correcto confirma el correo e inicia la sesión
    code = code_from_mail(mail)
    done = w.verify(email, code)
    assert done.status_code == 302 and done.headers["Location"] == "/"
    assert w.get("/").status_code == 200
    # y luego el ingreso normal funciona
    assert Web().login(email).status_code == 302


def test_scenario7_wrong_code_is_rejected_and_the_account_stays_unverified():
    w, email = Web(), unique_email()
    w.register(email)
    real = code_from_mail(latest_mail(email))
    wrong = "000000" if real != "000000" else "111111"
    resp = w.verify(email, wrong)
    assert resp.status_code == 400 and "incorrecto o vencido" in resp.text
    assert not w.s.cookies.get("hn_access")


def test_scenario7_brute_force_on_the_code_is_limited_to_5_attempts():
    w, email = Web(), unique_email()
    w.register(email)
    real = code_from_mail(latest_mail(email))
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        assert w.verify(email, wrong).status_code == 400
    assert w.verify(email, real).status_code == 400                         # el código ya quedó invalidado
    assert not w.s.cookies.get("hn_access")


def test_scenario7_privacy_consent_is_mandatory():
    w, email = Web(), unique_email()
    resp = w.register(email, consent=False)
    assert resp.status_code == 422 and "Debes aceptar la política de privacidad" in resp.text
    assert mail_count(email) == 0                                           # ni cuenta ni correo
    page = Web().get("/politica-de-privacidad")
    assert page.status_code == 200 and "Ley N.° 29733" in page.text


def test_scenario7_gmail_aliases_do_not_create_duplicate_accounts():
    base = f"qa{int(time.time())}x"
    first = f"{base[:4]}.{base[4:]}@gmail.com"
    w = Web()
    assert w.register(first).status_code == 302
    w.verify(first)
    for alias in (f"{base}@gmail.com", f"{base}+otra@gmail.com", f"{base.upper()}@GOOGLEMAIL.COM"):
        resp = Web().register(alias)
        assert resp.status_code == 409 and "Ya existe una cuenta" in resp.text, alias


@pytest.mark.parametrize("first,last", [
    ("", "Quispe"), ("Lucía", ""), ("L", "Quispe"), ("Lucía3", "Quispe"), ("<script>alert(1)</script>", "Quispe"),
    ("Test", "Quispe"), ("Lucía", "aaaa"), ("Lucía", "Quispe;DROP TABLE users"), ("Lucía", "N" * 51),
])
def test_scenario8_real_first_and_last_names_are_required(first, last):
    email = unique_email()
    resp = Web().register(email, first=first, last=last)
    assert resp.status_code == 422, (first, last)
    assert mail_count(email) == 0


def test_scenario8_accents_hyphens_and_apostrophes_are_accepted():
    email = unique_email()
    assert Web().register(email, first="Ana-María", last="O'Brien Núñez").status_code == 302


# ========================================================= RF07 · actualizar
def test_rf07_refresh_button_is_rate_limited_and_reports_state(member_web):
    first = member_web.api("POST", "/api/v1/weather/refresh")
    second = member_web.api("POST", "/api/v1/weather/refresh")
    if 502 in (first.status_code, second.status_code):                       # el proveedor real falló: contrato RN08
        failed = first if first.status_code == 502 else second
        assert failed.json()["detail"] == "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."
        return
    assert second.json()["refreshed"] is False                               # dentro del intervalo mínimo
    assert second.json()["current"] and second.json()["current"]["stale"] is False


# ========================================================= RF08 · historial
def test_rf08_history_records_have_the_required_fields(member_web):
    body = member_web.api("GET", "/api/v1/weather/history?source=current&page_size=5").json()
    assert body["total"] >= 1
    record = body["items"][0]
    for field in ("id", "location", "date", "time", "temperature", "humidity", "precipitation",
                  "rain_probability", "wind_speed", "cloud_cover", "condition"):
        assert field in record, field
    assert re.match(r"\d{4}-\d\d-\d\d$", record["date"]) and re.match(r"\d\d:\d\d$", record["time"])
