"""Escenarios 7 y 8 en la interfaz: nombre y apellido, consentimiento y confirmación con código."""

import json

import requests

from conftest import AUTH, USER, cookie_header, csrf_from, login_cookies, set_pending_email, tokens

FORM = {"first_name": "Ana", "last_name": "Ríos", "email": "ana@gmail.com", "password": "clave1234",
        "password_confirm": "clave1234", "accept_privacy": "on"}


def post_register(client, **overrides):
    data = {**FORM, **overrides, "csrf_token": csrf_from(client, "/registro")}
    return client.post("/registro", data={k: v for k, v in data.items() if v is not None})


def test_register_form_asks_for_first_and_last_name_and_consent(client):
    html = client.get("/registro").get_data(as_text=True)
    for expected in ('name="first_name"', 'name="last_name"', 'name="email"', 'name="accept_privacy"',
                     "política de privacidad", "Declaro que mis datos son reales", "Gmail"):
        assert expected in html
    assert 'href="/politica-de-privacidad"' in html and "required" in html.split('name="accept_privacy"')[1][:120]


def test_privacy_policy_page_is_public_and_versioned(client):
    resp = client.get("/politica-de-privacidad")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    for expected in ("Política de privacidad", "Versión 2026-10-03", "Ley N.° 29733", "revisado por asesoría legal"):
        assert expected in html


def test_register_success_sends_data_to_auth_and_goes_to_code_screen(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/register", status=201,
                json={"status": "verification_required", "email": "ana@gmail.com", "expires_in": 600})
    resp = post_register(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/verificar")
    assert json.loads(mocked.calls[0].request.body) == {
        "first_name": "Ana", "last_name": "Ríos", "email": "ana@gmail.com", "password": "clave1234",
        "accept_privacy": True}
    assert cookie_header(resp, "hn_access") is None                   # todavía sin sesión
    page = client.get("/verificar").get_data(as_text=True)
    assert "a***@gmail.com" in page and "ana@gmail.com" not in page     # el correo se muestra enmascarado
    assert 'name="code"' in page and 'autocomplete="one-time-code"' in page


def test_register_without_consent_shows_all_errors_together(client, mocked):
    """QA D2: antes solo se veía el error de consentimiento y los demás aparecían después."""
    mocked.post(f"{AUTH}/api/v1/auth/register", status=422, json={"code": "VALIDATION_ERROR", "errors": [
        {"field": "first_name", "message": "Ingresa tu nombre real."},
        {"field": "accept_privacy", "message": "Debes aceptar la política de privacidad para crear la cuenta."}]})
    resp = post_register(client, first_name="Test", accept_privacy=None)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 422
    assert "Debes aceptar la política de privacidad" in html and "Ingresa tu nombre real." in html
    assert json.loads(mocked.calls[0].request.body)["accept_privacy"] is False      # el servicio rechaza sin crear nada


def test_register_password_mismatch_is_blocked_locally(client, mocked):
    resp = post_register(client, password_confirm="otra1234")
    assert resp.status_code == 422 and "Las contraseñas no coinciden." in resp.get_data(as_text=True)
    assert len(mocked.calls) == 0


def test_register_shows_name_errors_from_the_service_and_keeps_the_values(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/register", status=422, json={
        "code": "VALIDATION_ERROR", "errors": [{"field": "first_name", "message": "Ingresa tu nombre real."}]})
    resp = post_register(client, first_name="Test")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 422 and "Ingresa tu nombre real." in html and 'value="Test"' in html


def test_register_email_already_taken(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/register", status=409,
                json={"code": "EMAIL_TAKEN", "detail": "Ya existe una cuenta con ese correo."})
    resp = post_register(client)
    assert resp.status_code == 409 and "Ya existe una cuenta con ese correo." in resp.get_data(as_text=True)


def test_register_when_mail_delivery_fails_lets_the_user_resend(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/register", status=503,
                json={"code": "EMAIL_DELIVERY_FAILED", "detail": "No pudimos enviar el correo de confirmación."})
    resp = post_register(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/verificar")
    assert "No pudimos enviar el correo" in client.get("/verificar").get_data(as_text=True)


def test_register_requires_csrf(client, mocked):
    assert client.post("/registro", data=FORM).status_code == 400 and len(mocked.calls) == 0


def test_code_screen_needs_a_pending_registration(client):
    resp = client.get("/verificar")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/ingresar")


def test_correct_code_confirms_and_starts_the_session(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/verify-email", json=tokens("acc", "ref"))
    set_pending_email(client)
    token = csrf_from(client, "/verificar")
    resp = client.post("/verificar", data={"code": "123 456", "csrf_token": token})     # admite espacios
    assert resp.status_code == 302 and resp.headers["Location"] == "/"
    assert json.loads(mocked.calls[0].request.body) == {"email": "ana@example.com", "code": "123456"}
    assert "hn_access=acc" in cookie_header(resp, "hn_access") and "HttpOnly" in cookie_header(resp, "hn_access")
    assert client.get("/verificar").status_code == 302                                   # ya no hay pendiente


def test_wrong_code_shows_the_error_and_keeps_waiting(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/verify-email", status=400,
                json={"code": "INVALID_CODE", "detail": "Código incorrecto o vencido. Solicita uno nuevo si hace falta."})
    set_pending_email(client)
    resp = client.post("/verificar", data={"code": "000000", "csrf_token": csrf_from(client, "/verificar")})
    assert resp.status_code == 400 and "Código incorrecto o vencido" in resp.get_data(as_text=True)
    assert cookie_header(resp, "hn_access") is None


def test_malformed_code_is_rejected_without_calling_the_service(client, mocked):
    set_pending_email(client)
    token = csrf_from(client, "/verificar")
    for bad in ("", "12345", "abcdef", "1234567", "12 34"):
        resp = client.post("/verificar", data={"code": bad, "csrf_token": token})
        assert resp.status_code == 422 and "6 dígitos" in resp.get_data(as_text=True), bad
    assert len(mocked.calls) == 0


def test_code_form_requires_csrf(client, mocked):
    set_pending_email(client)
    client.get("/verificar")
    assert client.post("/verificar", data={"code": "123456"}).status_code == 400
    assert len(mocked.calls) == 0


def test_resend_code_calls_auth_and_confirms_generically(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/resend-code", status=202, json={"status": "accepted"})
    set_pending_email(client)
    token = csrf_from(client, "/verificar")
    resp = client.post("/verificar/reenviar", data={"csrf_token": token}, follow_redirects=True)
    assert "te enviamos un código nuevo" in resp.get_data(as_text=True)
    assert json.loads(mocked.calls[0].request.body) == {"email": "ana@example.com"}


def test_login_with_unconfirmed_email_redirects_to_the_code_screen(client, mocked):
    mocked.post(f"{AUTH}/api/v1/auth/login", status=403,
                json={"code": "EMAIL_NOT_VERIFIED", "email": "ana@example.com", "detail": "Confirma tu correo."})
    token = csrf_from(client, "/ingresar")
    resp = client.post("/ingresar", data={"email": "ana@example.com", "password": "clave1234", "csrf_token": token},
                       follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert resp.request.path == "/verificar" and "Ingresa tu código" in html
    assert "aún no está confirmado" in html and cookie_header(resp, "hn_access") is None


def test_header_shows_the_user_full_name_escaped(client, mocked):
    mocked.get(f"{AUTH}/api/v1/auth/me", json={**USER, "full_name": "<img src=x onerror=alert(1)> Ríos"})
    login_cookies(client)
    html = client.get("/").get_data(as_text=True)
    assert "<img src=x onerror=alert(1)>" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html


def test_flash_messages_are_dismissible_and_expire(client, mocked):
    """QA D3: el aviso quedaba fijo sobre el contenido indefinidamente."""
    mocked.post(f"{AUTH}/api/v1/auth/register", status=201,
                json={"status": "verification_required", "email": "ana@gmail.com"})
    post_register(client)
    html = client.get("/verificar").get_data(as_text=True)
    assert 'data-flash="6000"' in html and "data-flash-close" in html and 'aria-label="Cerrar aviso"' in html


def test_footer_text_meets_contrast_and_calendar_padding_has_no_opacity(client):
    """QA D5: axe-core marcó `text-outline` sobre fondo beige y las celdas atenuadas por opacidad."""
    html = client.get("/politica-de-privacidad").get_data(as_text=True)
    assert "text-outline" not in html
    js = open("app/static/js/app.js", encoding="utf-8").read()
    assert "opacity-30" not in js
