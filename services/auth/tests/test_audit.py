"""Auditoría de seguridad: qué eventos quedan registrados, qué datos NO se guardan y quién puede consultarlos."""

from datetime import timedelta

import pytest

from app import audit
from app.audit import Event
from app.extensions import db
from app.models import AuditEvent
from conftest import PASSWORD, last_code, register_payload
from honolulo_common.timeutil import utcnow

BASE = "/api/v1/auth"
FROM_WEB = {"X-Client-IP": "203.0.113.9"}


def events(app, *names):
    with app.app_context():
        rows = db.session.execute(db.select(AuditEvent).order_by(AuditEvent.id)).scalars().all()
        return [r for r in rows if not names or r.event in names]


def names(app, *only):
    return [r.event for r in events(app, *only)]


def login(client, email="ana@example.com", password=PASSWORD, headers=None):
    return client.post(f"{BASE}/login", json={"email": email, "password": password}, headers=headers)


def make_admin(app, client):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-admin", "--email", "jefe@example.com"],
                           input="Jefe\nHonolulo\nVerde-Selva-742\nVerde-Selva-742\n")
    assert result.exit_code == 0, result.output
    resp = login(client, "jefe@example.com", "Verde-Selva-742")
    return {"Authorization": f"Bearer {resp.get_json()['access_token']}"}


# ------------------------------------------------------------------ registro y confirmación
def test_registration_and_confirmation_are_recorded(app, client):
    client.post(f"{BASE}/register", json=register_payload(), headers=FROM_WEB)
    client.post(f"{BASE}/verify-email", json={"email": "ana@example.com", "code": last_code(app)}, headers=FROM_WEB)
    assert names(app) == ["registered", "email_verified"]
    first = events(app)[0]
    assert first.user_id is not None and first.ip == "203.0.113.9"
    assert first.email_hint == "a***@example.com" and len(first.email_hash) == 16


def test_failed_confirmations_and_resends_are_recorded(app, client):
    client.post(f"{BASE}/register", json=register_payload())
    client.post(f"{BASE}/verify-email", json={"email": "ana@example.com", "code": "000000"})
    client.post(f"{BASE}/verify-email", json={"email": "nadie@example.com", "code": "000000"})
    client.post(f"{BASE}/resend-code", json={"email": "ana@example.com"})
    rows = events(app, "verify_failed", "code_resent")
    assert [r.event for r in rows] == ["verify_failed", "verify_failed", "code_resent"]
    assert rows[0].detail["reason"] == "wrong_or_expired_code" and rows[1].detail["reason"] == "no_pending_account"
    assert rows[1].user_id is None and rows[1].email_hint == "n***@example.com"


# --------------------------------------------------------------------------- sesión
def test_logins_logouts_and_unverified_attempts_are_recorded(app, client, registered):
    ok = login(client, headers=FROM_WEB)
    client.post(f"{BASE}/logout", json={"refresh_token": ok.get_json()["refresh_token"]})
    assert names(app, "login_success", "logout") == ["login_success", "logout"]
    assert events(app, "login_success")[0].ip == "203.0.113.9"
    client.post(f"{BASE}/register", json=register_payload(email="otra@example.com", first_name="Eva"))
    assert login(client, "otra@example.com").status_code == 403
    assert names(app, "login_unverified") == ["login_unverified"]


def test_failed_logins_are_recorded_with_the_reason_and_without_the_email_in_clear(app, client, registered):
    login(client, password="Mala-Clave-9999")
    login(client, "no-existe@example.com", "Otra-Clave-9999")
    rows = events(app, "login_failed")
    assert [r.detail["reason"] for r in rows] == ["bad_credentials", "bad_credentials"]
    assert rows[0].user_id is not None and rows[1].user_id is None


def test_lockout_and_blocked_attempts_are_recorded(app, client, registered):
    for _ in range(5):
        login(client, password="Mala-Clave-9999")
    assert login(client).status_code == 429
    assert names(app, "login_failed", "account_locked", "login_blocked") == (
        ["login_failed"] * 5 + ["account_locked", "login_blocked"])
    assert events(app, "account_locked")[0].detail["minutes"] == 15
    assert events(app, "login_blocked")[0].detail["wait_s"] >= 1


def test_refresh_token_reuse_is_recorded_as_a_possible_theft(app, client, registered):
    tokens = login(client).get_json()
    first = client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200
    again = client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == 401
    [row] = events(app, "refresh_reuse")
    assert row.user_id is not None and len(row.detail["family"]) == 8


# -------------------------------------------------------------- lo que NO se guarda
def test_no_secret_or_full_email_ever_reaches_the_audit_table(app, client):
    client.post(f"{BASE}/register", json=register_payload(email="Privado.Real@Example.com"))
    code = last_code(app)
    client.post(f"{BASE}/verify-email", json={"email": "privado.real@example.com", "code": "999999"})
    client.post(f"{BASE}/verify-email", json={"email": "privado.real@example.com", "code": code})
    login(client, "privado.real@example.com", "Mala-Clave-9999")
    login(client, "privado.real@example.com", PASSWORD)
    dump = " ".join(str(serialized) for serialized in (audit.serialize(r) for r in events(app)))
    for secret in (PASSWORD, "Mala-Clave-9999", code, "999999", "privado.real@example.com", "Privado.Real", "scrypt", "pbkdf2"):
        assert secret not in dump, secret


def test_the_email_fingerprint_groups_equivalent_addresses_and_is_keyed(app):
    with app.app_context():
        same = {audit.fingerprint(e) for e in ("ana.perez@gmail.com", "AnaPerez+x@gmail.com", "anaperez@googlemail.com")}
        assert len(same) == 1 and None not in same
        assert audit.fingerprint("otra@gmail.com") not in same and audit.fingerprint(None) is None
        assert len(audit.fingerprint("a@b.co")) == 16 and "a@b.co" not in audit.fingerprint("a@b.co")
        app.config["JWT_SECRET_KEY"], previous = "otra-clave-otra-clave-otra-clave-1234", app.config["JWT_SECRET_KEY"]
        try:
            assert audit.fingerprint("ana.perez@gmail.com") not in same            # la huella depende de la clave
        finally:
            app.config["JWT_SECRET_KEY"] = previous


@pytest.mark.parametrize("header,expected", [("203.0.113.9", "203.0.113.9"), ("2001:db8::1", "2001:db8::1"),
                                             ("no-es-una-ip", "127.0.0.1"), ("999.1.1.1", "127.0.0.1"),
                                             ("1.1.1.1, 2.2.2.2", "127.0.0.1"), ("<script>", "127.0.0.1")])
def test_only_valid_ip_addresses_are_accepted_from_the_gateway_header(app, client, header, expected):
    login(client, headers={"X-Client-IP": header})
    assert events(app, "login_failed")[0].ip == expected


# ------------------------------------------------------------ administración y consulta
def test_role_changes_and_admin_creation_from_the_command_line_are_recorded(app, client, registered):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["set-role", "ana@example.com", "admin"]).exit_code == 0
    assert runner.invoke(args=["set-role", "ana@example.com", "admin"]).exit_code == 0      # sin cambio: no se registra otra vez
    assert runner.invoke(args=["set-role", "ana@example.com", "user"]).exit_code == 0
    rows = events(app, "role_changed")
    assert [(r.detail["old"], r.detail["new"], r.detail["source"]) for r in rows] == [("user", "admin", "cli"), ("admin", "user", "cli")]
    make_admin(app, client)
    assert names(app, "admin_created") == ["admin_created"]
    assert runner.invoke(args=["create-admin", "--email", "ana@example.com"]).exit_code == 0
    assert names(app, "role_changed")[-1] == "role_changed" and events(app, "role_changed")[-1].detail["new"] == "admin"


def test_only_admins_can_read_the_audit_log(app, client, registered):
    assert client.get(f"{BASE}/admin/audit").status_code == 401
    user = {"Authorization": f"Bearer {registered['access_token']}"}
    assert client.get(f"{BASE}/admin/audit", headers=user).status_code == 403
    admin = make_admin(app, client)
    resp = client.get(f"{BASE}/admin/audit", headers=admin)
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert "login_success" in resp.get_json()["events"] and resp.get_json()["total"] >= 1


def test_audit_listing_is_newest_first_paged_and_filterable(app, client, registered):
    for _ in range(3):
        login(client, password="Mala-Clave-9999")
    admin = make_admin(app, client)
    body = client.get(f"{BASE}/admin/audit?limit=2", headers=admin).get_json()
    assert len(body["items"]) == 2 and body["items"][0]["id"] > body["items"][1]["id"] and body["total"] > 2
    failed = client.get(f"{BASE}/admin/audit?event=login_failed", headers=admin).get_json()
    assert failed["total"] == 3 and {i["event"] for i in failed["items"]} == {"login_failed"}
    assert client.get(f"{BASE}/admin/audit?offset=1000", headers=admin).get_json()["items"] == []


@pytest.mark.parametrize("query", ["event=inventado", "limit=0", "limit=201", "limit=abc", "offset=-1"])
def test_audit_listing_validates_its_parameters(app, client, query):
    admin = make_admin(app, client)
    assert client.get(f"{BASE}/admin/audit?{query}", headers=admin).status_code == 422


def test_audit_events_older_than_the_retention_period_can_be_purged(app, client, registered):
    login(client)
    with app.app_context():
        old = AuditEvent(event="login_success", occurred_at=utcnow() - timedelta(days=400), ip="1.2.3.4")
        db.session.add(old)
        db.session.commit()
    runner = app.test_cli_runner()
    result = runner.invoke(args=["purge-audit"])
    assert result.exit_code == 0 and "borrados" in result.output and "1" in result.output
    assert all(r.ip != "1.2.3.4" for r in events(app))
    assert names(app, "login_success")                                   # lo reciente se conserva
    assert runner.invoke(args=["purge-audit", "--days", "0"]).exit_code == 0 and events(app) == []


def test_the_audit_list_command_prints_recent_events(app, client, registered):
    login(client, password="Mala-Clave-9999", headers=FROM_WEB)
    out = app.test_cli_runner().invoke(args=["audit-list", "--limit", "5"]).output
    assert "login_failed" in out and "203.0.113.9" in out and "a***@example.com" in out


def test_the_audit_does_not_change_what_the_api_returns(app, client, registered):
    ok, bad = login(client), login(client, password="Mala-Clave-9999")
    assert ok.status_code == 200 and bad.status_code == 401 and bad.get_json()["code"] == "INVALID_CREDENTIALS"
