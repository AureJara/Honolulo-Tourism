"""Seguridad reforzada: contraseñas, creación de administradores sin dejar claves a la vista y envío REAL del
código de confirmación por SMTP (contra un servidor SMTP local de pruebas, sin salir a Internet)."""

import logging
import secrets

import pytest

from app import mail_templates, mailer
from app.models import User
from app.extensions import db
from conftest import PASSWORD, last_code, outbox, register_payload
from honolulo_common.testing import FakeSmtpServer, code_from_message

BASE = "/api/v1/auth"


def register(client, **overrides):
    return client.post(f"{BASE}/register", json=register_payload(**overrides))


def fields(resp):
    return {e["field"]: e["message"] for e in resp.get_json()["errors"]}


# --------------------------------------------------------------- contraseñas
def test_password_cannot_contain_the_email_or_the_name(client):
    resp = register(client, first_name="Marcelo", last_name="Quispe", email="marcelo.quispe@example.com",
                    password="Quispe-Rio-2026")
    assert resp.status_code == 422 and "nombre" in fields(resp)["password"].lower()
    ok = register(client, first_name="Marcelo", last_name="Quispe", email="marcelo.quispe@example.com",
                  password="Verde-Selva-742")
    assert ok.status_code == 201


def test_password_errors_never_echo_the_password(client):
    secret = "Contraseña2026"
    resp = register(client, password=secret)
    assert resp.status_code == 422 and secret not in resp.get_data(as_text=True)


def test_password_is_stored_only_as_a_salted_hash(app, client):
    assert register(client).status_code == 201
    with app.app_context():
        stored = db.session.execute(db.select(User)).scalar_one().password_hash
    assert PASSWORD not in stored and ":" in stored and len(stored) > 60
    # Dos usuarios con la misma contraseña no comparten hash (sal aleatoria)
    assert register(client, email="otra@example.com").status_code == 201
    with app.app_context():
        hashes = {u.password_hash for u in db.session.execute(db.select(User)).scalars()}
    assert len(hashes) == 2


def test_passwords_never_reach_the_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    register(client)
    client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": PASSWORD})
    client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": "Otra-Clave-9999"})
    assert PASSWORD not in caplog.text and "Otra-Clave-9999" not in caplog.text


# ------------------------------------------------------------ crear administrador
def test_create_admin_asks_for_a_hidden_password_and_applies_the_policy(app, client):
    runner = app.test_cli_runner()
    weak = runner.invoke(args=["create-admin", "--email", "jefe@example.com"],
                         input="Jefe\nHonolulo\npassword123\npassword123\n")
    assert weak.exit_code != 0 and "demasiado común" in weak.output
    with app.app_context():
        assert db.session.execute(db.select(User)).first() is None

    good = runner.invoke(args=["create-admin", "--email", "jefe@example.com"],
                         input="Jefe\nHonolulo\nVerde-Selva-742\nVerde-Selva-742\n")
    assert good.exit_code == 0 and "Administrador listo" in good.output
    assert "Verde-Selva-742" not in good.output           # la clave se escribe sin eco y nunca se imprime
    with app.app_context():
        user = db.session.execute(db.select(User)).scalar_one()
        assert user.role == "admin" and user.email_verified_at is not None
        assert user.password_hash != "Verde-Selva-742"
    assert client.post(f"{BASE}/login", json={"email": "jefe@example.com", "password": "Verde-Selva-742"}).status_code == 200


def test_promoting_an_existing_user_does_not_ask_for_a_password(app, client):
    assert register(client).status_code == 201
    result = app.test_cli_runner().invoke(args=["create-admin", "--email", "ana@example.com"])
    assert result.exit_code == 0 and "Contraseña" not in result.output
    with app.app_context():
        assert db.session.execute(db.select(User)).scalar_one().role == "admin"


def test_admin_passwords_must_differ_from_personal_data(app, client):
    result = app.test_cli_runner().invoke(args=["create-admin", "--email", "jefe@example.com"],
                                          input="Jefe\nHonolulo\nHonolulo-2026-x\nHonolulo-2026-x\n")
    assert result.exit_code != 0 and "nombre" in result.output.lower()


# ------------------------------------------------------ envío REAL por SMTP (Escenario 7)
@pytest.fixture()
def smtp(app, monkeypatch, tmp_path):
    """Servicio configurado para enviar por SMTP a un servidor local de pruebas."""
    user, password = "remitente@example.com", secrets.token_hex(12)
    server = FakeSmtpServer(require_auth=(user, password))
    for key, value in {"MAIL_BACKEND": "smtp", "SMTP_HOST": server.host, "SMTP_PORT": server.port,
                       "SMTP_SECURITY": "none", "SMTP_USER": user, "SMTP_PASSWORD": password,
                       "MAIL_FROM": f"Honolulo <{user}>", "SMTP_TIMEOUT_S": 3,
                       "MAIL_OUTBOX_DIR": str(tmp_path / "outbox")}.items():
        monkeypatch.setitem(app.config, key, value)
    server.password = password
    yield server
    server.close()


def test_the_code_is_emailed_to_the_address_used_to_register(app, client, smtp):
    resp = register(client, email="Visitante.Real@Example.com")
    assert resp.status_code == 201 and outbox(app) == []               # nada quedó solo en memoria
    [delivery] = smtp.messages
    assert delivery["to"] == ["visitante.real@example.com"]
    assert smtp.credentials == [("remitente@example.com", smtp.password)]
    message = smtp.parsed()
    assert message["To"] == "visitante.real@example.com" and "remitente@example.com" in message["From"]
    code = code_from_message(message)
    assert code in message["Subject"]
    # El código que llegó al correo es el que de verdad confirma la cuenta
    done = client.post(f"{BASE}/verify-email", json={"email": "visitante.real@example.com", "code": code})
    assert done.status_code == 200 and done.get_json()["user"]["email_verified"] is True


def test_the_code_is_not_written_to_logs_or_files_when_smtp_is_used(app, client, smtp, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    assert register(client).status_code == 201
    message = smtp.parsed()
    code = code_from_message(message)
    assert code not in caplog.text and smtp.password not in caplog.text
    assert not (tmp_path / "outbox").exists()                           # el modo archivo no se usó


def test_resend_code_also_goes_to_the_users_mailbox(app, client, smtp):
    from conftest import age_verifications
    register(client)
    age_verifications(app, 120)
    assert client.post(f"{BASE}/resend-code", json={"email": "ana@example.com"}).status_code == 202
    assert len(smtp.messages) == 2 and smtp.messages[1]["to"] == ["ana@example.com"]


def test_smtp_rejection_is_reported_without_leaking_the_code(app, client, monkeypatch, caplog):
    server = FakeSmtpServer(fail_with="451 intenta más tarde")
    captured = []
    real = mail_templates.build_verification_message
    monkeypatch.setattr(mailer, "build_verification_message",
                        lambda to, first, code, ttl: captured.append(code) or real(to, first, code, ttl))
    for key, value in {"MAIL_BACKEND": "smtp", "SMTP_HOST": server.host, "SMTP_PORT": server.port,
                       "SMTP_SECURITY": "none", "SMTP_USER": "", "SMTP_TIMEOUT_S": 3}.items():
        monkeypatch.setitem(app.config, key, value)
    caplog.set_level(logging.DEBUG)
    try:
        resp = register(client)
    finally:
        server.close()
    body = resp.get_json()
    assert resp.status_code == 503 and body["code"] == "EMAIL_DELIVERY_FAILED"
    assert captured and captured[0] not in resp.get_data(as_text=True) and captured[0] not in caplog.text


def test_unreachable_smtp_server_gives_a_clear_error(app, client, monkeypatch):
    for key, value in {"MAIL_BACKEND": "smtp", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": 9, "SMTP_SECURITY": "none",
                       "SMTP_USER": "", "SMTP_TIMEOUT_S": 2}.items():
        monkeypatch.setitem(app.config, key, value)
    resp = register(client)
    assert resp.status_code == 503 and resp.get_json()["code"] == "EMAIL_DELIVERY_FAILED"


def test_wrong_smtp_credentials_do_not_log_the_password(app, client, monkeypatch, caplog):
    server = FakeSmtpServer(require_auth=("remitente@example.com", "la-correcta"))
    wrong = secrets.token_hex(10)
    for key, value in {"MAIL_BACKEND": "smtp", "SMTP_HOST": server.host, "SMTP_PORT": server.port,
                       "SMTP_SECURITY": "none", "SMTP_USER": "remitente@example.com", "SMTP_PASSWORD": wrong,
                       "SMTP_TIMEOUT_S": 3}.items():
        monkeypatch.setitem(app.config, key, value)
    caplog.set_level(logging.DEBUG)
    try:
        resp = register(client)
    finally:
        server.close()
    assert resp.status_code == 503 and wrong not in caplog.text and wrong not in resp.get_data(as_text=True)


def test_mail_test_command_sends_through_smtp(app, smtp):
    result = app.test_cli_runner().invoke(args=["mail-test", "yo@example.com"])
    assert result.exit_code == 0 and "enviado a yo@example.com" in result.output
    assert smtp.messages[0]["to"] == ["yo@example.com"] and smtp.password not in result.output


def test_mail_test_command_warns_when_nothing_leaves_the_machine(app, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_BACKEND", "memory")
    result = app.test_cli_runner().invoke(args=["mail-test", "yo@example.com"])
    assert result.exit_code == 0 and "NO salió a Internet" in result.output
