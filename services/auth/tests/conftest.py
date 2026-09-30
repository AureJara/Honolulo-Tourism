import re
from datetime import timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import EmailVerification
from honolulo_common.testing import create_test_database
from honolulo_common.timeutil import utcnow

PASSWORD = "clave1234"


@pytest.fixture(scope="session")
def app():
    url = create_test_database("auth_test")
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": url,
        "JWT_SECRET_KEY": "test-secret-test-secret-test-secret-123",
        "LOCKOUT_MINUTES": 15,
        "MAIL_BACKEND": "memory",
        "PASSWORD_HASH_METHOD": "pbkdf2:sha256:1000",      # barato: solo para que las pruebas sean rápidas
        "VERIFICATION_RESEND_SECONDS": 60,
    })
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture()
def client(app):
    with app.app_context():
        names = ", ".join(f'"{t.name}"' for t in db.metadata.sorted_tables)
        db.session.execute(db.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        db.session.commit()
    app.extensions["mail_outbox"] = []
    return app.test_client()


def outbox(app):
    return app.extensions.get("mail_outbox", [])


def last_code(app, to=None) -> str:
    """Código de 6 dígitos del último correo enviado (a ``to`` si se indica)."""
    messages = [m for m in outbox(app) if to is None or m["To"] == to]
    assert messages, "no se envió ningún correo"
    return re.search(r"\b(\d{6})\b", messages[-1].get_body(("plain",)).get_content()).group(1)


def register_payload(**overrides):
    payload = {"first_name": "Ana", "last_name": "Ríos", "email": "Ana@Example.com",
               "password": PASSWORD, "accept_privacy": True}
    payload.update(overrides)
    return payload


def age_verifications(app, seconds):
    """Retrocede en el tiempo los códigos emitidos (simula que pasó ``seconds``)."""
    with app.app_context():
        for row in db.session.execute(db.select(EmailVerification)).scalars():
            row.created_at = row.created_at - timedelta(seconds=seconds)
            row.expires_at = row.expires_at - timedelta(seconds=seconds)
        db.session.commit()


@pytest.fixture()
def registered(app, client):
    """Usuario registrado y con el correo confirmado; devuelve la respuesta de verify-email."""
    assert client.post("/api/v1/auth/register", json=register_payload()).status_code == 201
    resp = client.post("/api/v1/auth/verify-email", json={"email": "ana@example.com", "code": last_code(app)})
    assert resp.status_code == 200
    return resp.get_json()
