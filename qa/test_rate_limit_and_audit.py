"""Límite de peticiones por IP y auditoría de seguridad, contra el sistema real.

El sistema de la QA corre sin límite por IP (``--no-rate-limit``: las pruebas hacen cientos de registros desde una misma
IP), así que aquí se levanta una instancia APARTE de la web con el límite activo y confiando en un proxy delante
(``TRUSTED_PROXY_HOPS=1``), para poder simular IP distintas con ``X-Forwarded-For``. Habla con los mismos servicios.
"""

import random
import time

import pytest
import requests

from conftest import AUTH_URL, unique_email
from honolulo_common.testing import search_or_fail
from test_resilience import running  # noqa: F401  (fixture ``running``)

XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture()
def strict(running):
    """Base URL de una web con el límite de peticiones activo."""
    return f"http://127.0.0.1:{running('web', 8012, RATE_LIMIT_ENABLED='1', TRUSTED_PROXY_HOPS='1').port}"


def random_ip() -> str:
    return f"198.51.100.{random.randint(2, 250)}"


class Visitor:
    """Navegador simulado hacia una web concreta, desde una IP concreta (vía X-Forwarded-For)."""

    def __init__(self, base: str, ip: str) -> None:
        self.base, self.ip, self.s = base, ip, requests.Session()

    def post_form(self, path, data):
        page = self.s.get(self.base + "/ingresar", headers={"X-Forwarded-For": self.ip}, timeout=20)
        token = search_or_fail(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        return self.s.post(self.base + path, data={"csrf_token": token, **data}, allow_redirects=False,
                           headers={"X-Forwarded-For": self.ip}, timeout=30)

    def request(self, method, path, **kw):
        return self.s.request(method, self.base + path, headers={"X-Forwarded-For": self.ip, **kw.pop("headers", {})},
                              allow_redirects=False, timeout=30, **kw)


def bad_login(visitor, n=1):
    return [visitor.post_form("/ingresar", {"email": unique_email("qa.limite"), "password": "Clave-Incorrecta-1"})
            for _ in range(n)]


# ----------------------------------------------------------------- límite por IP
def test_password_guessing_is_stopped_after_ten_attempts_from_one_ip(strict):
    visitor = Visitor(strict, random_ip())
    first = bad_login(visitor, 10)
    assert all(r.status_code != 429 for r in first)
    blocked = bad_login(visitor)[0]
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1
    assert "Demasiados intentos de ingreso" in blocked.text


def test_a_blocked_ip_does_not_affect_other_people_behind_the_same_proxy(strict):
    attacker, neighbour = Visitor(strict, random_ip()), Visitor(strict, "203.0.113.200")
    bad_login(attacker, 11)
    assert bad_login(attacker)[0].status_code == 429
    assert bad_login(neighbour)[0].status_code != 429
    assert neighbour.request("GET", "/politica-de-cookies").status_code == 200


def test_the_forwarded_header_cannot_be_rotated_to_escape_when_the_proxy_is_not_trusted(running):
    """Con la web por defecto (sin proxy de confianza) ``X-Forwarded-For`` se ignora: rotarlo no sirve de nada."""
    base = f"http://127.0.0.1:{running('web', 8013, RATE_LIMIT_ENABLED='1', TRUSTED_PROXY_HOPS='0').port}"
    s = requests.Session()
    page = s.get(base + "/ingresar", timeout=20)
    token = search_or_fail(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    codes = [s.post(base + "/ingresar", data={"csrf_token": token, "email": unique_email(), "password": "Clave-Incorrecta-1"},
                    headers={"X-Forwarded-For": f"9.9.9.{i}"}, allow_redirects=False, timeout=30).status_code for i in range(12)]
    assert codes[-1] == 429


def test_mass_account_creation_is_limited(strict):
    visitor = Visitor(strict, random_ip())
    statuses = [visitor.post_form("/registro", {"first_name": "Lucía", "last_name": "Quispe", "email": unique_email(),
                                                "password": "Clave-Incorrecta-1", "password_confirm": "otra"}).status_code
                for _ in range(11)]
    assert 429 not in statuses[:10] and statuses[10] == 429


def test_api_writes_are_limited_with_a_machine_readable_answer(strict):
    visitor = Visitor(strict, random_ip())
    path = "/api/v1/places/catarata-velo-de-las-ninfas/reviews/mine"
    codes = [visitor.request("PUT", path, json={"rating": 5}, headers=XHR).status_code for _ in range(21)]
    assert set(codes[:20]) == {401} and codes[20] == 429
    blocked = visitor.request("PUT", path, json={"rating": 5}, headers=XHR)
    body = blocked.json()
    assert blocked.headers["Content-Type"].startswith("application/problem+json") and body["code"] == "RATE_LIMITED"
    assert body["retry_after"] >= 1 and blocked.headers["Retry-After"] == str(body["retry_after"])


def test_static_files_and_photos_do_not_count_against_the_global_limit(strict):
    visitor = Visitor(strict, random_ip())
    assert all(visitor.request("GET", "/static/css/app.css").status_code == 200 for _ in range(320))
    assert visitor.request("GET", "/politica-de-cookies").status_code == 200


# ---------------------------------------------------------------------- auditoría
def audit_items(admin_token, **params):
    resp = requests.get(f"{AUTH_URL}/api/v1/auth/admin/audit", params={"limit": 200, **params}, timeout=20,
                        headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_real_client_ip_reaches_the_audit_log_through_the_gateway(strict, admin_token):
    ip, email = f"203.0.113.{random.randint(2, 250)}", unique_email("qa.auditoria")
    Visitor(strict, ip).post_form("/ingresar", {"email": email, "password": "Clave-Incorrecta-1"})
    time.sleep(0.3)
    mine = [i for i in audit_items(admin_token, event="login_failed")["items"] if i["ip"] == ip]
    assert len(mine) == 1
    item = mine[0]
    assert item["email_hint"] == "q***@example.com" and item["user_id"] is None and item["detail"]["reason"] == "bad_credentials"
    assert email not in str(item) and "Clave-Incorrecta" not in str(item)             # ni el correo completo ni la clave


def test_logins_and_logouts_of_a_real_member_are_audited(member, admin_token):
    from conftest import Web
    web = Web()
    assert web.login(member["email"]).status_code == 302
    web.form("/salir", {}, csrf_path="/registro")
    events = [i["event"] for i in audit_items(admin_token)["items"]]
    assert "login_success" in events and "logout" in events


def test_only_admins_can_read_the_audit_log_and_the_security_page(member_token, member_web, admin_web, admin_token):
    url = f"{AUTH_URL}/api/v1/auth/admin/audit"
    assert requests.get(url, timeout=20).status_code == 401
    assert requests.get(url, headers={"Authorization": f"Bearer {member_token}"}, timeout=20).status_code == 403
    assert member_web.get("/admin/seguridad").status_code == 403
    page = admin_web.get("/admin/seguridad")
    assert page.status_code == 200 and "Actividad de seguridad" in page.text and "Fecha (UTC)" in page.text
    filtered = admin_web.get("/admin/seguridad?evento=login_success")
    assert filtered.status_code == 200 and "Ingreso correcto" in filtered.text
    assert admin_web.get("/admin/seguridad?evento=inventado").status_code == 200       # filtro inválido: mensaje, no error


def test_the_audit_page_never_shows_full_emails_or_secrets(admin_web, member):
    html = admin_web.get("/admin/seguridad").text
    assert member["email"] not in html and "scrypt" not in html and "password" not in html.lower().replace("password_", "")
