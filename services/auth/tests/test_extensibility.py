"""Principio abierto/cerrado: se puede añadir un backend de correo, una forma de conectar por SMTP o una regla de
contraseña escribiendo código nuevo, sin modificar el que ya existe."""

import pytest

from app import create_app, mailer, passwords
from conftest import PASSWORD, register_payload

BASE = "/api/v1/auth"
SECRET = "k" * 48


@pytest.fixture()
def registries(monkeypatch):
    """Registros aislados: lo que se registre en una prueba no sale de ella."""
    monkeypatch.setattr(mailer, "_BACKENDS", dict(mailer._BACKENDS))
    monkeypatch.setattr(mailer, "_SMTP_SECURITY", dict(mailer._SMTP_SECURITY))
    monkeypatch.setattr(passwords, "_RULES", list(passwords._RULES))


# ------------------------------------------------------------------ backends de correo
def test_a_new_mail_backend_works_without_touching_the_mailer(app, client, registries, monkeypatch):
    delivered = []

    @mailer.register_backend("captura", external=False, describe=lambda: "una lista en memoria")
    def _capture(message):
        delivered.append(message)

    monkeypatch.setitem(app.config, "MAIL_BACKEND", "captura")
    assert client.post(f"{BASE}/register", json=register_payload()).status_code == 201
    assert [m["To"] for m in delivered] == ["ana@example.com"]
    assert mailer.backend("captura").describe() == "una lista en memoria" and mailer.backend("captura").external is False


def test_builtin_backends_declare_whether_they_leave_the_machine():
    assert [mailer.backend(n).external for n in ("memory", "file", "smtp")] == [False, False, True]


def test_an_unknown_mail_backend_is_a_clear_error(app, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_BACKEND", "paloma-mensajera")
    with app.app_context(), pytest.raises(mailer.MailError, match="paloma-mensajera"):
        mailer.send(mailer.build_verification_message("a@b.co", "Ana", "123456", 10))


def test_production_requires_a_backend_that_really_delivers(registries):
    base = {"APP_ENV": "production", "JWT_SECRET_KEY": SECRET}
    with pytest.raises(RuntimeError, match="debe enviar por Internet"):
        create_app({**base, "MAIL_BACKEND": "file"})
    create_app({**base, "MAIL_BACKEND": "smtp"})

    @mailer.register_backend("api-externa", external=True)
    def _api(message):
        raise NotImplementedError

    create_app({**base, "MAIL_BACKEND": "api-externa"})           # un backend nuevo que sale a Internet también vale


# --------------------------------------------------------------- conexión SMTP
def test_a_misspelled_smtp_security_never_degrades_to_plain_text(app, monkeypatch):
    for key, value in {"MAIL_BACKEND": "smtp", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": 9, "SMTP_SECURITY": "startls"}.items():
        monkeypatch.setitem(app.config, key, value)
    with app.app_context(), pytest.raises(mailer.MailError, match="SMTP_SECURITY desconocido"):
        mailer.send(mailer.build_verification_message("a@b.co", "Ana", "123456", 10))


def test_a_new_smtp_security_mode_is_picked_up_by_name(app, registries, monkeypatch):
    opened = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send_message(self, message):
            opened.append(message["To"])

    @mailer.register_smtp_security("tunel")
    def _tunnel(cfg):
        return FakeClient()

    for key, value in {"MAIL_BACKEND": "smtp", "SMTP_HOST": "relay.interno", "SMTP_SECURITY": "tunel", "SMTP_USER": ""}.items():
        monkeypatch.setitem(app.config, key, value)
    with app.app_context():
        mailer.send(mailer.build_verification_message("ana@example.com", "Ana", "123456", 10))
    assert opened == ["ana@example.com"]


# --------------------------------------------------------------------- contraseñas
def test_a_new_password_rule_is_enforced_by_check_password_and_the_register_endpoint(client, registries):
    @passwords.password_rule
    def _no_letter_q(candidate):
        return "No uses la letra Q." if "q" in candidate.folded else None

    assert passwords.check_password("Verde-Selva-742", min_len=10) is None
    assert passwords.check_password("Quebrada-Verde-742", min_len=10) == "No uses la letra Q."
    resp = client.post(f"{BASE}/register", json=register_payload(password="Quebrada-Verde-742"))
    assert resp.status_code == 422 and "letra Q" in str(resp.get_json())


def test_rules_run_in_registration_order_and_the_first_failure_wins():
    assert passwords.check_password("corta1", min_len=10).startswith("Debe tener entre")          # longitud primero
    assert passwords.check_password("soloLetrasAquiYAhora", min_len=10) == "Debe combinar letras y números."
    assert "común" in passwords.check_password("password123", min_len=10)


def test_the_default_policy_is_unchanged_by_the_refactor():
    assert passwords.check_password(PASSWORD, min_len=10) is None
    assert passwords.check_password("Ana-Rios-2026x", min_len=10, personal=("Ana", "Ríos")) .startswith("No debe contener")
    assert passwords.check_password("aaaaaaaaaa1", min_len=10) == "Usa más variedad de caracteres."
