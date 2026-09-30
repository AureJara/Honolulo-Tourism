import pytest

from conftest import PASSWORD, register_payload

BASE = "/api/v1/auth"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_verified_user_session_has_expected_shape(registered):
    user = registered["user"]
    assert user["email"] == "ana@example.com"
    assert (user["first_name"], user["last_name"], user["full_name"]) == ("Ana", "Ríos", "Ana Ríos")
    assert user["role"] == "user" and user["email_verified"] is True
    assert registered["token_type"] == "Bearer" and registered["expires_in"] == 900
    assert "password" not in str(registered)


def test_login_success_and_me(client, registered):
    resp = client.post(f"{BASE}/login", json={"email": "ANA@example.com", "password": PASSWORD})
    assert resp.status_code == 200
    me = client.get(f"{BASE}/me", headers=auth(resp.get_json()["access_token"]))
    assert me.status_code == 200 and me.get_json()["full_name"] == "Ana Ríos"


def test_login_wrong_password_and_unknown_user_share_message(client, registered):
    bad = client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": "incorrecta1"})
    unknown = client.post(f"{BASE}/login", json={"email": "nadie@example.com", "password": "incorrecta1"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.get_json()["detail"] == unknown.get_json()["detail"]
    assert bad.get_json()["code"] == "INVALID_CREDENTIALS"


def test_account_locks_after_repeated_failures(client, registered):
    for _ in range(5):
        client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": "mala-clave1"})
    locked = client.post(f"{BASE}/login", json={"email": "ana@example.com", "password": PASSWORD})
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert locked.get_json()["code"] == "ACCOUNT_LOCKED"


def test_me_requires_valid_access_token(client, registered):
    assert client.get(f"{BASE}/me").status_code == 401
    assert client.get(f"{BASE}/me", headers=auth("basura")).status_code == 401
    # un refresh token no sirve como access token
    assert client.get(f"{BASE}/me", headers=auth(registered["refresh_token"])).status_code == 401


def test_refresh_rotates_and_detects_reuse(client, registered):
    first = registered["refresh_token"]
    resp = client.post(f"{BASE}/refresh", json={"refresh_token": first})
    assert resp.status_code == 200
    second = resp.get_json()["refresh_token"]
    assert second != first
    assert client.get(f"{BASE}/me", headers=auth(resp.get_json()["access_token"])).status_code == 200

    # Reusar el token ya rotado invalida toda la familia, incluido el token nuevo.
    reuse = client.post(f"{BASE}/refresh", json={"refresh_token": first})
    assert reuse.status_code == 401
    assert client.post(f"{BASE}/refresh", json={"refresh_token": second}).status_code == 401


def test_refresh_with_unknown_token_is_401(client):
    resp = client.post(f"{BASE}/refresh", json={"refresh_token": "x" * 40})
    assert resp.status_code == 401 and resp.get_json()["code"] == "INVALID_REFRESH_TOKEN"


def test_logout_revokes_refresh_token(client, registered):
    token = registered["refresh_token"]
    assert client.post(f"{BASE}/logout", json={"refresh_token": token}).status_code == 204
    assert client.post(f"{BASE}/refresh", json={"refresh_token": token}).status_code == 401
    # idempotente
    assert client.post(f"{BASE}/logout", json={"refresh_token": token}).status_code == 204


def test_login_with_gmail_alias_reaches_the_same_account(app, client):
    from conftest import last_code
    client.post(f"{BASE}/register", json=register_payload(email="Mi.Correo@gmail.com"))
    client.post(f"{BASE}/verify-email", json={"email": "mi.correo@gmail.com", "code": last_code(app)})
    for alias in ("micorreo@gmail.com", "mi.correo+hn@gmail.com", "m.i.c.o.r.r.e.o@googlemail.com"):
        resp = client.post(f"{BASE}/login", json={"email": alias, "password": PASSWORD})
        assert resp.status_code == 200 and resp.get_json()["user"]["email"] == "mi.correo@gmail.com", alias


def test_health_endpoints(client):
    assert client.get("/healthz").get_json()["status"] == "ok"
    assert client.get("/readyz").status_code == 200
