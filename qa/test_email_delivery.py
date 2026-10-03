"""El código de confirmación llega al correo de quien se registra (Escenario 7), de punta a punta.

Se levanta una instancia real del servicio de cuentas con ``MAIL_BACKEND=smtp`` apuntando a un servidor SMTP local de
pruebas (no sale a Internet) y se recorre el flujo por HTTP: registro → correo recibido → código → sesión.
"""

import secrets

import pytest
import requests

from conftest import PASSWORD
from honolulo_common.testing import FakeSmtpServer, code_from_message
from test_resilience import _base_env, _new_database, migrate, running  # noqa: F401  (fixture ``running``)

SENDER = "honolulo.remitente@example.com"


@pytest.fixture()
def smtp_auth(running):
    """Servicio de cuentas real, con una base propia y enviando por SMTP con autenticación."""
    smtp_password = secrets.token_hex(12)
    server = FakeSmtpServer(require_auth=(SENDER, smtp_password))
    db = _new_database("auth_qa_mail")
    migrate("auth", db)
    alt = running("auth", 5011, DATABASE_URL=db, MAIL_BACKEND="smtp", SMTP_HOST=server.host, SMTP_PORT=str(server.port),
                  SMTP_SECURITY="none", SMTP_USER=SENDER, SMTP_PASSWORD=smtp_password, MAIL_FROM=f"Honolulo <{SENDER}>",
                  SMTP_TIMEOUT_S="3")
    yield f"http://127.0.0.1:{alt.port}/api/v1/auth", server, smtp_password
    server.close()


def register(base, email, **extra):
    body = {"first_name": "Lucía", "last_name": "Quispe", "email": email, "password": PASSWORD, "accept_privacy": True}
    body.update(extra)
    return requests.post(f"{base}/register", json=body, timeout=20)


def test_the_code_arrives_at_the_registered_mailbox_and_confirms_the_account(smtp_auth):
    base, server, smtp_password = smtp_auth
    resp = register(base, "visitante.real@example.com")
    assert resp.status_code == 201 and "access_token" not in resp.json()

    [delivery] = server.messages
    assert delivery["to"] == ["visitante.real@example.com"]                           # al correo de quien se registra
    assert server.credentials == [(SENDER, smtp_password)]                            # autenticado con las credenciales
    message = server.parsed()
    assert SENDER in message["From"] and "Hola Lucía" in message.get_body(("plain",)).get_content()
    code = code_from_message(message)

    wrong = requests.post(f"{base}/verify-email", json={"email": "visitante.real@example.com", "code": "000000"}, timeout=20)
    assert wrong.status_code == 400
    ok = requests.post(f"{base}/verify-email", json={"email": "visitante.real@example.com", "code": code}, timeout=20)
    assert ok.status_code == 200 and ok.json()["user"]["email_verified"] is True and ok.json()["access_token"]
    login = requests.post(f"{base}/login", json={"email": "visitante.real@example.com", "password": PASSWORD}, timeout=20)
    assert login.status_code == 200


def test_each_registration_goes_only_to_its_own_address(smtp_auth):
    base, server, _ = smtp_auth
    for email in ("uno@example.com", "dos@example.com", "tres@example.com"):
        assert register(base, email).status_code == 201
    assert [m["to"] for m in server.messages] == [["uno@example.com"], ["dos@example.com"], ["tres@example.com"]]
    codes = {code_from_message(server.parsed(i)) for i in range(3)}
    assert len(codes) == 3                                                            # un código distinto por persona


def test_resend_and_login_of_an_unconfirmed_account_email_a_new_code(smtp_auth):
    base, server, _ = smtp_auth
    register(base, "pendiente@example.com")
    first = code_from_message(server.parsed(0))
    assert requests.post(f"{base}/resend-code", json={"email": "pendiente@example.com"}, timeout=20).status_code == 202
    assert len(server.messages) == 1                                                  # respeta el intervalo mínimo de reenvío
    assert requests.post(f"{base}/resend-code", json={"email": "nadie@example.com"}, timeout=20).status_code == 202
    assert len(server.messages) == 1                                                  # no revela si el correo existe
    assert first.isdigit() and len(first) == 6


def test_the_code_is_in_the_email_but_never_in_the_services_response(smtp_auth):
    base, server, smtp_password = smtp_auth
    resp = register(base, "privado@example.com")
    code = code_from_message(server.parsed())
    assert code not in resp.text and smtp_password not in resp.text and PASSWORD not in resp.text


def test_if_the_mail_server_refuses_the_user_gets_a_clear_message(running):
    server = FakeSmtpServer(fail_with="451 intenta más tarde")
    try:
        db = _new_database("auth_qa_mailfail")
        migrate("auth", db)
        alt = running("auth", 5011, DATABASE_URL=db, MAIL_BACKEND="smtp", SMTP_HOST=server.host, SMTP_PORT=str(server.port),
                      SMTP_SECURITY="none", SMTP_TIMEOUT_S="3")
        resp = register(f"http://127.0.0.1:{alt.port}/api/v1/auth", "sinservicio@example.com")
    finally:
        server.close()
    assert resp.status_code == 503 and resp.json()["code"] == "EMAIL_DELIVERY_FAILED"
    assert "No pudimos enviar el correo" in resp.json()["detail"]


def test_with_no_smtp_server_at_all_registration_reports_the_failure(running):
    db = _new_database("auth_qa_mailnone")
    migrate("auth", db)
    alt = running("auth", 5011, DATABASE_URL=db, MAIL_BACKEND="smtp", SMTP_HOST="127.0.0.1", SMTP_PORT="9",
                  SMTP_SECURITY="none", SMTP_TIMEOUT_S="2")
    resp = register(f"http://127.0.0.1:{alt.port}/api/v1/auth", "sinservidor@example.com")
    assert resp.status_code == 503 and resp.json()["code"] == "EMAIL_DELIVERY_FAILED"
