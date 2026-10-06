"""La respuesta dice si el código salió a Internet.

La web no debe afirmar «te enviamos un correo» cuando el mensaje se quedó en una carpeta (desarrollo), y en producción no
debe revelar nada de la configuración del envío."""

import pytest

from app import mailer
from conftest import PASSWORD, register_payload

BASE = "/api/v1/auth"


@pytest.fixture()
def registries(monkeypatch):
    """Registros aislados: lo que se registre en una prueba no sale de ella."""
    monkeypatch.setattr(mailer, "_BACKENDS", dict(mailer._BACKENDS))


def register(client):
    resp = client.post(f"{BASE}/register", json=register_payload())
    assert resp.status_code == 201
    return resp.get_json()


def test_a_backend_that_keeps_the_message_local_says_so(client):
    assert register(client)["delivery"] == {"external": False, "where": ""}          # «memory» de las pruebas


def test_the_file_backend_says_where_the_code_went(app, client, monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, "MAIL_BACKEND", "file")
    monkeypatch.setitem(app.config, "MAIL_OUTBOX_DIR", str(tmp_path))
    delivery = register(client)["delivery"]
    assert delivery["external"] is False and str(tmp_path) in delivery["where"]


def test_an_external_backend_only_reports_that_the_message_left_the_machine(app, client, registries, monkeypatch):
    """En producción no se revela nada de la configuración: ni carpetas ni servidores."""
    sent = []

    @mailer.register_backend("saliente-prueba", external=True, describe=lambda: "detalle interno que no debe salir")
    def _outgoing(message):
        sent.append(message)

    monkeypatch.setitem(app.config, "MAIL_BACKEND", "saliente-prueba")
    body = register(client)
    assert body["delivery"] == {"external": True} and len(sent) == 1
    assert "detalle interno" not in str(body)


def test_resend_reports_the_delivery_the_same_for_known_and_unknown_emails(client):
    """No se puede usar para averiguar qué correos están registrados."""
    register(client)
    known = client.post(f"{BASE}/resend-code", json={"email": "ana@example.com"})
    unknown = client.post(f"{BASE}/resend-code", json={"email": "nadie@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.get_json()["delivery"] == unknown.get_json()["delivery"] == {"external": False, "where": ""}


def test_logging_in_before_confirming_the_email_reports_the_delivery(client):
    register(client)
    resp = client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": PASSWORD})
    assert resp.status_code == 403 and resp.get_json()["code"] == "EMAIL_NOT_VERIFIED"
    assert resp.get_json()["delivery"] == {"external": False, "where": ""}


def test_an_unknown_backend_does_not_break_the_answer(app, client, monkeypatch):
    """Si la configuración está mal, el envío ya falla con un error claro; informar el modo no debe romper nada."""
    monkeypatch.setitem(app.config, "MAIL_BACKEND", "paloma-mensajera")
    with app.app_context():
        assert mailer.delivery_info() == {"external": False, "where": ""}
