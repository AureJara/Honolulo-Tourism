"""Privacidad y secretos contra el sistema en marcha: la contraseña solo la conoce el usuario, el código no queda en
los registros, las políticas están publicadas y el sitio no usa más cookies que las declaradas."""

import re
import uuid

import pytest
import requests

from conftest import BASE, ROOT, Web, auth_db, code_from_mail, latest_mail, unique_email

LOGS = ROOT / ".logs"
DOCUMENTED_COOKIES = {"hn_access", "hn_refresh", "hn_csrf", "hn_session"}


def logs_text() -> str:
    files = sorted(LOGS.glob("*.log")) if LOGS.exists() else []
    if not files:
        pytest.skip("No hay registros en .logs/ (el sistema no se inició con scripts/dev.py)")
    return "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in files)


def database_text() -> str:
    """Todo el contenido de la base de cuentas, tabla por tabla, como texto."""
    chunks = []
    with auth_db() as conn:
        for (table,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall():
            chunks += [row[0] for row in conn.execute(f'SELECT "{table}"::text FROM "{table}"').fetchall()]
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def canary_account():
    """Cuenta nueva con una contraseña única e inconfundible, recorriendo el flujo completo."""
    canary = "Cn-" + uuid.uuid4().hex[:12] + "-Qz9"
    email = unique_email("qa.canary")
    web = Web()
    assert web.register(email, first="Marcela", last="Ramos", password=canary).status_code == 302
    mail = latest_mail(email)
    code = code_from_mail(mail)
    assert web.verify(email, code).status_code == 302
    again = Web()
    assert again.login(email, canary).status_code == 302
    assert Web().login(email, canary + "x").status_code in (401, 422)          # un intento fallido también deja rastro
    return {"canary": canary, "email": email, "code": code, "mail": mail, "web": again}


# ------------------------------------------------- la contraseña no se ve en ningún lado
def test_the_password_never_appears_in_the_logs(canary_account):
    assert canary_account["canary"] not in logs_text()
    assert canary_account["canary"] + "x" not in logs_text()


def test_the_password_is_only_a_salted_hash_in_the_database(canary_account):
    assert canary_account["canary"] not in database_text()
    with auth_db() as conn:
        (stored,) = conn.execute("SELECT password_hash FROM users WHERE email_canonical = %s",
                                 (canary_account["email"],)).fetchone()
    assert stored.startswith(("scrypt:", "pbkdf2:")) and len(stored) > 60


def test_the_password_is_never_in_the_email(canary_account):
    assert canary_account["canary"] not in canary_account["mail"]


def test_the_confirmation_code_is_not_written_to_the_logs(canary_account):
    code = canary_account["code"]
    assert not re.search(rf"(?i)(c[oó]digo|code|otp)\W{{0,40}}{code}", logs_text())


def test_only_a_hash_of_the_code_is_stored(canary_account):
    code = canary_account["code"]
    assert not re.search(rf"\b{code}\b", database_text())


def test_error_pages_do_not_echo_the_password(canary_account):
    web = Web()
    resp = web.register(unique_email(), first="Marcela", last="Ramos", password=canary_account["canary"], consent=False)
    assert resp.status_code in (422, 400) and canary_account["canary"] not in resp.text
    bad = web.form("/registro", {"first_name": "Ana", "last_name": "Ríos", "email": unique_email(),
                                 "password": canary_account["canary"], "password_confirm": "otra-distinta-1"})
    assert bad.status_code == 422 and canary_account["canary"] not in bad.text


# ------------------------------------------------------------- política de contraseñas
@pytest.mark.parametrize("password,fragment", [
    ("password123", "común"), ("Contraseña2026", "común"), ("corta1A", "entre 10"), ("soloLetrasAquiYAhora", "letras y números"),
    ("Marcela-Ramos-26", "nombre"),
])
def test_weak_passwords_are_rejected_by_the_live_form(password, fragment):
    web = Web()
    resp = web.register(unique_email("qa.weak"), first="Marcela", last="Ramos", password=password)
    assert resp.status_code == 422 and fragment in resp.text, password


def test_a_password_containing_the_email_is_rejected():
    web = Web()
    email = "valentina.zegarra@example.com"
    resp = web.register(email, first="Valentina", last="Zegarra", password="Zegarra-Cielo-81")
    assert resp.status_code == 422 and "nombre" in resp.text


def test_the_accepted_policy_version_is_recorded(canary_account):
    with auth_db() as conn:
        (version,) = conn.execute("SELECT privacy_policy_version FROM users WHERE email_canonical = %s",
                                  (canary_account["email"],)).fetchone()
    assert version == "2026-10-04"


# ---------------------------------------------------------------------- políticas
def test_privacy_and_cookie_policies_are_published():
    privacy = requests.get(f"{BASE}/politica-de-privacidad", timeout=20)
    cookies = requests.get(f"{BASE}/politica-de-cookies", timeout=20)
    assert privacy.status_code == 200 and cookies.status_code == 200
    for expected in ("Opiniones y puntuaciones", "Proveedor de correo", "hash", "derechos ARCO", "Versión 2026-10-04"):
        assert expected in privacy.text
    for name in DOCUMENTED_COOKIES:
        assert name in cookies.text
    assert "solo cookies esenciales" in cookies.text


def test_every_page_links_both_policies_and_shows_the_cookie_notice():
    for path in ("/ingresar", "/registro", "/politica-de-privacidad"):
        html = requests.get(f"{BASE}{path}", timeout=20).text
        assert "/politica-de-privacidad" in html and "/politica-de-cookies" in html and 'id="cookie-notice"' in html


def test_the_site_sets_only_the_documented_cookies_and_all_are_httponly(canary_account):
    jar = canary_account["web"].s.cookies
    names = {c.name for c in jar}
    assert names and names <= DOCUMENTED_COOKIES
    assert all(c.has_nonstandard_attr("HttpOnly") for c in jar)


def test_security_headers_on_html_pages():
    for path in ("/ingresar", "/politica-de-cookies"):
        h = requests.get(f"{BASE}{path}", timeout=20).headers
        csp = h["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp and "form-action 'self'" in csp and "base-uri 'self'" in csp
        assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
        assert "camera=()" in h["Permissions-Policy"] and h["Referrer-Policy"]


def test_no_secret_value_is_exposed_by_any_public_route():
    secrets_file = (ROOT / ".env.local").read_text(encoding="utf-8")
    values = [line.split("=", 1)[1].strip() for line in secrets_file.splitlines()
              if "=" in line and not line.startswith("#") and len(line.split("=", 1)[1].strip()) >= 16]
    assert values, "no se encontraron secretos locales que comparar"
    for path in ("/", "/ingresar", "/registro", "/readyz", "/healthz", "/static/js/app.js", "/static/js/reviews.js",
                 "/api/v1/places", "/politica-de-privacidad", "/no-existe", "/api/v1/weather/current"):
        resp = requests.get(f"{BASE}{path}", timeout=20, allow_redirects=True)
        blob = resp.text + str(resp.headers) + "".join(f"{c.name}={c.value}" for c in resp.cookies)
        for value in values:
            assert value not in blob, f"{path} expone un secreto"
