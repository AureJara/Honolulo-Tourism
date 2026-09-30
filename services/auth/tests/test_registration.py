"""Escenarios 7 y 8 del spec: correo con código de confirmación, consentimiento de privacidad y
nombre y apellido reales. Más los casos límite y de abuso que un QA probaría."""

import pytest

from app import mailer
from app.extensions import db
from app.models import EmailVerification, User
from app.names import InvalidName, clean_person_name
from conftest import PASSWORD, age_verifications, last_code, outbox, register_payload

BASE = "/api/v1/auth"


def register(client, **overrides):
    return client.post(f"{BASE}/register", json=register_payload(**overrides))


def verify(client, email, code):
    return client.post(f"{BASE}/verify-email", json={"email": email, "code": code})


# --------------------------------------------------- Escenario 7: código por correo
def test_register_sends_a_6_digit_code_and_issues_no_session_yet(app, client):
    resp = register(client)
    body = resp.get_json()
    assert resp.status_code == 201 and body["status"] == "verification_required"
    assert body["email"] == "ana@example.com" and body["expires_in"] == 600 and body["resend_after"] == 60
    assert "access_token" not in body and "refresh_token" not in body

    [message] = outbox(app)
    assert message["To"] == "ana@example.com"
    assert message["Subject"].startswith("Tu código de confirmación de Honolulo")
    code = last_code(app)
    assert len(code) == 6 and code.isdigit()
    assert "Hola Ana" in message.get_body(("plain",)).get_content()
    assert "Vence en 10 minutos" in message.get_body(("plain",)).get_content()


def test_login_is_blocked_until_the_email_is_confirmed(app, client):
    register(client)
    resp = client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": PASSWORD})
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["code"] == "EMAIL_NOT_VERIFIED" and body["email"] == "ana@example.com"
    # y no se puede usar ningún token: no se emitió ninguno
    assert "access_token" not in body


def test_correct_code_confirms_the_email_and_starts_the_session(app, client):
    register(client)
    resp = verify(client, "ana@example.com", last_code(app))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["user"]["email_verified"] is True and body["access_token"]
    me = client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": PASSWORD}).status_code == 200


def test_code_is_single_use(app, client):
    register(client)
    code = last_code(app)
    assert verify(client, "ana@example.com", code).status_code == 200
    assert verify(client, "ana@example.com", code).status_code == 400


def test_wrong_code_is_rejected_and_codes_are_never_stored_in_clear(app, client):
    register(client)
    code = last_code(app)
    wrong = "000000" if code != "000000" else "111111"
    resp = verify(client, "ana@example.com", wrong)
    assert resp.status_code == 400 and resp.get_json()["code"] == "INVALID_CODE"
    with app.app_context():
        row = db.session.execute(db.select(EmailVerification)).scalar_one()
        assert code not in row.code_hash and len(row.code_hash) == 64 and row.attempts == 1


def test_code_is_locked_after_too_many_wrong_attempts_even_if_later_guessed(app, client):
    """Fuerza bruta: 10^6 combinaciones y solo 5 intentos por código."""
    register(client)
    code = last_code(app)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert verify(client, "ana@example.com", wrong).status_code == 400
    assert verify(client, "ana@example.com", code).status_code == 400          # ya invalidado
    with app.app_context():
        assert db.session.execute(db.select(User)).scalar_one().email_verified_at is None


def test_expired_code_is_rejected(app, client):
    register(client)
    code = last_code(app)
    age_verifications(app, 11 * 60)
    assert verify(client, "ana@example.com", code).status_code == 400


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 345", "１２３４５６", None, 123456])
def test_malformed_codes_are_422(app, client, code):
    register(client)
    resp = client.post(f"{BASE}/verify-email", json={"email": "ana@example.com", "code": code})
    assert resp.status_code == 422


def test_verify_for_unknown_or_already_verified_email_is_generic_400(app, client, registered):
    assert verify(client, "nadie@example.com", "123456").status_code == 400
    assert verify(client, "ana@example.com", "123456").status_code == 400


def test_a_new_code_invalidates_the_previous_one(app, client):
    register(client)
    first = last_code(app)
    age_verifications(app, 61)
    client.post(f"{BASE}/resend-code", json={"email": "ana@example.com"})
    second = last_code(app)
    assert len(outbox(app)) == 2
    if first != second:
        assert verify(client, "ana@example.com", first).status_code == 400
    assert verify(client, "ana@example.com", second).status_code == 200


def test_resend_is_throttled_and_always_answers_202(app, client):
    register(client)
    assert len(outbox(app)) == 1
    for _ in range(3):                                   # dentro de los 60 s: no se envía nada más
        assert client.post(f"{BASE}/resend-code", json={"email": "ana@example.com"}).status_code == 202
    assert len(outbox(app)) == 1
    age_verifications(app, 61)
    assert client.post(f"{BASE}/resend-code", json={"email": "ana@example.com"}).status_code == 202
    assert len(outbox(app)) == 2


def test_resend_does_not_reveal_whether_an_email_is_registered(app, client, registered):
    known = client.post(f"{BASE}/resend-code", json={"email": "ana@example.com"})
    unknown = client.post(f"{BASE}/resend-code", json={"email": "nadie@example.com"})
    assert known.status_code == unknown.status_code == 202 and known.get_json() == unknown.get_json()
    assert outbox(app)[-1]["To"] == "ana@example.com" and len(outbox(app)) == 1      # solo el del registro


def test_at_most_five_codes_per_hour_per_account(app, client):
    register(client)
    for _ in range(10):
        age_verifications(app, 61)
        client.post(f"{BASE}/resend-code", json={"email": "ana@example.com"})
    assert len(outbox(app)) == 5                          # tope anti-spam


def test_login_with_unconfirmed_email_sends_a_fresh_code(app, client):
    register(client)
    age_verifications(app, 61)
    client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": PASSWORD})
    assert len(outbox(app)) == 2
    assert client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": "otra-clave1"}).status_code == 401
    assert len(outbox(app)) == 2                          # una contraseña errónea no dispara correos


def test_mail_delivery_failure_is_a_clean_503(app, client, monkeypatch):
    def boom(*a, **k):
        raise mailer.MailError("smtp caído")
    monkeypatch.setattr(mailer, "send_verification_email", boom)
    resp = register(client)
    assert resp.status_code == 503 and resp.get_json()["code"] == "EMAIL_DELIVERY_FAILED"
    # la cuenta quedó pendiente: el usuario puede pedir el código de nuevo
    monkeypatch.undo()
    age_verifications(app, 61)
    client.post(f"{BASE}/resend-code", json={"email": "ana@example.com"})
    assert len(outbox(app)) == 1


# -------------------------------------------- Escenario 7: consentimiento de privacidad
@pytest.mark.parametrize("value", [False, None, "no", 0])
def test_privacy_consent_is_mandatory(client, value):
    resp = register(client, accept_privacy=value)
    assert resp.status_code == 422
    assert "accept_privacy" in [e["field"] for e in resp.get_json()["errors"]]


def test_missing_consent_field_is_rejected(client):
    payload = register_payload()
    del payload["accept_privacy"]
    assert client.post(f"{BASE}/register", json=payload).status_code == 422


def test_consent_and_policy_version_are_recorded(app, client):
    register(client)
    with app.app_context():
        user = db.session.execute(db.select(User)).scalar_one()
        assert user.privacy_accepted_at is not None and user.privacy_policy_version == "2026-10"


# --------------------------------------------- Escenario 8: nombre y apellido reales
def test_first_and_last_name_are_required(client):
    for missing in ("first_name", "last_name"):
        payload = register_payload()
        del payload[missing]
        resp = client.post(f"{BASE}/register", json=payload)
        assert resp.status_code == 422 and missing in [e["field"] for e in resp.get_json()["errors"]]


@pytest.mark.parametrize("bad", ["", " ", "A", "Ana123", "<script>", "Ana_", "J@ime", "aaaa", "Test", "Usuario",
                                 "-Ana", "Ana-", "Ana--Maria", "x" * 51, "1234", "Ana;DROP TABLE users", "😀😀"])
def test_implausible_names_are_rejected(client, bad):
    for field in ("first_name", "last_name"):
        resp = register(client, **{field: bad})
        assert resp.status_code == 422, (field, bad)
        assert field in [e["field"] for e in resp.get_json()["errors"]]


@pytest.mark.parametrize("good", ["Ana", "María José", "O'Brien", "Jean-Luc", "Núñez", "De la Cruz", "Ñandú",
                                  "Yamada", "Åsa", "Ibáñez-Quispe"])
def test_real_looking_names_are_accepted(app, client, good):
    assert register(client, first_name=good, last_name=good).status_code == 201


def test_names_are_normalized(app, client):
    register(client, first_name="  Ana   María ", last_name="Ríos ")
    with app.app_context():
        user = db.session.execute(db.select(User)).scalar_one()
        assert (user.first_name, user.last_name) == ("Ana María", "Ríos")


def test_clean_person_name_unit():
    assert clean_person_name("  Ana   Lucía ") == "Ana Lucía"
    with pytest.raises(InvalidName):
        clean_person_name("Ana3")


# ------------------------------------------ duplicados, Gmail y cuentas sin confirmar
def test_verified_email_cannot_be_registered_again(app, client, registered):
    resp = register(client)
    assert resp.status_code == 409 and resp.get_json()["code"] == "EMAIL_TAKEN"


def test_gmail_aliases_do_not_create_duplicate_accounts(app, client):
    register(client, email="persona.uno@gmail.com")
    verify(client, "persona.uno@gmail.com", last_code(app))
    for alias in ("personauno@gmail.com", "persona.uno+promo@gmail.com", "PERSONAUNO@googlemail.com"):
        assert register(client, email=alias).status_code == 409, alias


def test_non_gmail_dots_and_plus_are_different_accounts(app, client):
    register(client, email="a.b@example.com")
    verify(client, "a.b@example.com", last_code(app, "a.b@example.com"))
    assert register(client, email="ab@example.com").status_code == 201


def test_registering_again_before_confirming_updates_the_pending_account(app, client):
    register(client, first_name="Ana", password="primera1234")
    age_verifications(app, 61)
    resp = register(client, first_name="Anita", password="segunda1234")
    assert resp.status_code == 201
    with app.app_context():
        assert db.session.execute(db.select(db.func.count()).select_from(User)).scalar_one() == 1
    assert verify(client, "ana@example.com", last_code(app)).status_code == 200
    # quedó la contraseña y el nombre más recientes
    assert client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": "primera1234"}).status_code == 401
    ok = client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": "segunda1234"})
    assert ok.status_code == 200 and ok.get_json()["user"]["first_name"] == "Anita"


def test_someone_else_cannot_hijack_a_pending_account_without_the_mailbox(app, client):
    """Un atacante que re-registra el correo de otra persona no recibe el código."""
    register(client, password="duena-legit1")
    age_verifications(app, 61)
    register(client, first_name="Intruso", password="atacante1234")
    # el atacante no tiene acceso al buzón: adivina al azar
    for guess in ("123456", "654321", "111111"):
        verify(client, "ana@example.com", guess)
    with app.app_context():
        assert db.session.execute(db.select(User)).scalar_one().email_verified_at is None


@pytest.mark.parametrize("password", ["corta1", "soloLetrasAqui", "123456789", "", "a" * 129 + "1"])
def test_password_policy(client, password):
    resp = register(client, password=password)
    assert resp.status_code == 422 and "password" in [e["field"] for e in resp.get_json()["errors"]]


@pytest.mark.parametrize("email", ["no-es-correo", "a@b", "@example.com", "a b@example.com", "x" * 250 + "@a.co", ""])
def test_invalid_emails_are_422(client, email):
    resp = register(client, email=email)
    assert resp.status_code == 422 and "email" in [e["field"] for e in resp.get_json()["errors"]]


def test_register_requires_a_json_object(client):
    assert client.post(f"{BASE}/register", data="x").status_code == 400
    assert client.post(f"{BASE}/register", json=[1, 2]).status_code == 400


def test_html_in_name_cannot_reach_the_verification_email(app, client):
    assert register(client, first_name="<b>Ana</b>").status_code == 422
    assert outbox(app) == []


def test_email_body_escapes_html_in_the_html_alternative():
    from app.mailer import _html
    assert _html("<img src=x onerror=alert(1)>") == "&lt;img src=x onerror=alert(1)&gt;"
