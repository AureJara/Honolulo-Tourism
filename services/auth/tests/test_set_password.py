"""``flask set-password``: restablecer la contraseña de una cuenta que ya existe (p. ej. la administradora que la olvidó).

La clave nueva se pide sin eco y nunca se imprime ni se guarda; se aplica la política del registro, la cuenta se desbloquea,
sus sesiones abiertas se cierran y el cambio queda en la auditoría."""

from datetime import timedelta

from app.audit import Event
from app.extensions import db
from app.models import AuditEvent, RefreshToken, User
from conftest import PASSWORD
from honolulo_common.timeutil import utcnow

BASE = "/api/v1/auth"
NEW_PASSWORD = "Rio-Azul-Monte-8841"


def login(client, email="jefe@example.com", password=PASSWORD):
    return client.post(f"{BASE}/login", json={"email": email, "password": password})


def make_admin(app, email="jefe@example.com", first="Jefe", last="Honolulo"):
    result = app.test_cli_runner().invoke(args=["create-admin", "--email", email],
                                          input=f"{first}\n{last}\n{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output


def set_password(app, email, *answers):
    return app.test_cli_runner().invoke(args=["set-password", "--email", email],
                                        input="".join(f"{answer}\n" for answer in answers))


def reset(app, email="jefe@example.com", password=NEW_PASSWORD):
    result = set_password(app, email, password, password)
    assert result.exit_code == 0, result.output
    return result


def test_the_new_password_works_the_old_one_stops_and_nothing_is_printed(app, client):
    make_admin(app)
    result = reset(app)
    assert "Contraseña actualizada" in result.output and NEW_PASSWORD not in result.output
    assert login(client, password=PASSWORD).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 200
    with app.app_context():
        stored = db.session.execute(db.select(User)).scalar_one().password_hash
    assert NEW_PASSWORD not in stored and stored != PASSWORD


def test_the_registration_policy_is_applied_and_the_old_password_is_kept(app, client):
    make_admin(app)
    weak = set_password(app, "jefe@example.com", "password123", "password123")
    assert weak.exit_code != 0 and "demasiado común" in weak.output
    personal = set_password(app, "jefe@example.com", "Honolulo-2026-x", "Honolulo-2026-x")
    assert personal.exit_code != 0 and "nombre" in personal.output.lower()
    assert login(client, password=PASSWORD).status_code == 200            # nada cambió


def test_a_mistyped_confirmation_changes_nothing(app, client):
    make_admin(app)
    result = set_password(app, "jefe@example.com", NEW_PASSWORD, "otra-cosa-distinta-1", NEW_PASSWORD, NEW_PASSWORD)
    # click vuelve a pedir ambas hasta que coinciden: al final queda la que se confirmó
    assert result.exit_code == 0 and NEW_PASSWORD not in result.output
    assert login(client, password=NEW_PASSWORD).status_code == 200


def test_an_unknown_account_is_reported_and_nothing_is_created(app, client):
    result = set_password(app, "nadie@example.com", NEW_PASSWORD, NEW_PASSWORD)
    assert result.exit_code != 0 and "no encontrado" in result.output.lower()
    with app.app_context():
        assert db.session.execute(db.select(User)).first() is None


def test_the_same_account_is_found_through_its_canonical_gmail_form(app, client):
    make_admin(app, email="ana.rios@gmail.com", first="Ana", last="Quispe")
    reset(app, email="Ana.Rios+prueba@gmail.com")
    assert login(client, "ana.rios@gmail.com", NEW_PASSWORD).status_code == 200


def test_it_unlocks_a_locked_account(app, client):
    make_admin(app)
    with app.app_context():
        user = db.session.execute(db.select(User)).scalar_one()
        user.failed_attempts, user.locked_until = 5, utcnow() + timedelta(minutes=15)
        db.session.commit()
    assert login(client).status_code == 429                                # bloqueada
    reset(app)
    assert login(client, password=NEW_PASSWORD).status_code == 200
    with app.app_context():
        user = db.session.execute(db.select(User)).scalar_one()
        assert user.failed_attempts == 0 and user.locked_until is None


def test_open_sessions_of_that_account_are_closed_and_others_are_not(app, client):
    make_admin(app)
    make_admin(app, email="otra@example.com", first="Otra", last="Persona")
    mine = login(client).get_json()["refresh_token"]
    other = login(client, "otra@example.com").get_json()["refresh_token"]
    reset(app)
    assert client.post(f"{BASE}/refresh", json={"refresh_token": mine}).status_code == 401
    assert client.post(f"{BASE}/refresh", json={"refresh_token": other}).status_code == 200
    with app.app_context():
        owner = db.session.execute(db.select(User).where(User.email == "jefe@example.com")).scalar_one()
        live = db.session.execute(db.select(RefreshToken).where(
            RefreshToken.user_id == owner.id, RefreshToken.revoked_at.is_(None))).first()
    assert live is None


def test_the_change_is_audited_without_the_password(app, client):
    make_admin(app)
    reset(app)
    with app.app_context():
        rows = db.session.execute(db.select(AuditEvent).where(AuditEvent.event == Event.PASSWORD_CHANGED.value)
                                  ).scalars().all()
    assert len(rows) == 1 and rows[0].user_id is not None
    assert rows[0].detail == {"source": "cli"} and NEW_PASSWORD not in str(rows[0].detail)
    assert rows[0].email_hint == "j***@example.com"
