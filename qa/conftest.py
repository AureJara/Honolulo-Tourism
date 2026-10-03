"""Pruebas de QA de extremo a extremo contra el sistema en ejecución (no usan mocks).

Requisitos: el sistema levantado (``python scripts/dev.py up``). Variables opcionales:
  QA_BASE_URL            (por defecto http://127.0.0.1:8000)
  QA_MAIL_DIR            carpeta donde el modo de desarrollo deja los correos (por defecto .mail/)
  QA_ADMIN_EMAIL/QA_ADMIN_PASSWORD   cuenta administradora (las pruebas de administración se omiten sin ella)
"""

from __future__ import annotations

import email as email_lib
import email.policy
import io
import os
import pathlib
import re
import secrets
import time
import uuid

import pytest
import requests
from PIL import Image

BASE = os.getenv("QA_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
AUTH_URL = os.getenv("QA_AUTH_URL", "http://127.0.0.1:5001")
CATALOG_URL = os.getenv("QA_CATALOG_URL", "http://127.0.0.1:5004")
ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIL_DIR = pathlib.Path(os.getenv("QA_MAIL_DIR", ROOT / ".mail"))
# Contraseña aleatoria solo para esta ejecución. No usa el esquema de los correos de prueba («qa.<hex>»): la política
# rechaza claves que contengan fragmentos del correo y, si coincidieran, la prueba fallaría al azar.
PASSWORD = "Zr-" + secrets.token_urlsafe(9) + "-8"


def pytest_collection_modifyitems(config, items):
    try:
        requests.get(f"{BASE}/healthz", timeout=3).raise_for_status()
    except requests.RequestException:
        skip = pytest.mark.skip(reason=f"El sistema no está corriendo en {BASE} (python scripts/dev.py up)")
        for item in items:
            item.add_marker(skip)


# ------------------------------------------------------------------ correo
def latest_mail(to: str, *, after: float = 0.0, wait: float = 6.0) -> str:
    """Último correo enviado a ``to`` (lo deja el modo ``file`` del servicio de autenticación)."""
    safe = re.sub(r"[^A-Za-z0-9@._-]", "_", to)
    deadline = time.time() + wait
    while time.time() < deadline:
        files = sorted(MAIL_DIR.glob(f"*-{safe}.txt"), key=lambda p: p.stat().st_mtime) if MAIL_DIR.exists() else []
        files = [f for f in files if f.stat().st_mtime >= after - 1]
        if files:
            return files[-1].read_text(encoding="utf-8")
        time.sleep(0.25)
    raise AssertionError(f"No llegó ningún correo a {to}")


def mail_count(to: str) -> int:
    safe = re.sub(r"[^A-Za-z0-9@._-]", "_", to)
    return len(list(MAIL_DIR.glob(f"*-{safe}.txt"))) if MAIL_DIR.exists() else 0


def parse_mail(text: str):
    """Mensaje decodificado (asunto RFC 2047 y cuerpo quoted-printable)."""
    return email_lib.message_from_string(text, policy=email.policy.default)


def mail_body(text: str) -> str:
    return parse_mail(text).get_body(("plain",)).get_content()


def code_from_mail(text: str) -> str:
    match = re.search(r"^\s{2,}(\d{6})\s*$", mail_body(text), re.MULTILINE)
    assert match, "el correo no contiene un código de 6 dígitos"
    return match.group(1)


# --------------------------------------------------------------- cliente web
class Web:
    """Navegador simulado: mantiene cookies y maneja el token CSRF de los formularios."""

    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "honolulo-qa"

    def get(self, path, **kw):
        return self.s.get(BASE + path, allow_redirects=kw.pop("allow_redirects", False), timeout=20, **kw)

    def csrf(self, path="/ingresar") -> str:
        html = self.get(path).text
        found = re.search(r'name="csrf_token" value="([^"]+)"', html)
        assert found, f"{path} no trae token CSRF"
        return found.group(1)

    def form(self, path, data, *, csrf_path=None, files=None, **kw):
        payload = {"csrf_token": self.csrf(csrf_path or path), **data}
        return self.s.post(BASE + path, data=payload, files=files, allow_redirects=kw.pop("allow_redirects", False),
                           timeout=30, **kw)

    def api(self, method, path, **kw):
        headers = {"X-Requested-With": "XMLHttpRequest"} if method.upper() != "GET" else {}
        return self.s.request(method, BASE + path, headers={**headers, **kw.pop("headers", {})}, timeout=30,
                              allow_redirects=False, **kw)

    def register(self, email, *, first="Lucía", last="Quispe", password=PASSWORD, consent=True, **extra):
        data = {"first_name": first, "last_name": last, "email": email, "password": password,
                "password_confirm": password, **extra}
        if consent:
            data["accept_privacy"] = "on"
        return self.form("/registro", data)

    def verify(self, email, code=None):
        code = code or code_from_mail(latest_mail(email))
        return self.form("/verificar", {"code": code})

    def login(self, email, password=PASSWORD, nxt="/"):
        return self.form("/ingresar", {"email": email, "password": password, "next": nxt})


def unique_email(prefix="qa", domain="example.com") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@{domain}"


@pytest.fixture()
def web():
    return Web()


@pytest.fixture(scope="session")
def member():
    """Usuario registrado y confirmado (flujo completo con código por correo)."""
    w, email = Web(), unique_email()
    assert w.register(email).status_code == 302
    assert w.verify(email).status_code == 302
    return {"email": email, "password": PASSWORD, "web": w}


@pytest.fixture()
def member_web(member):
    w = Web()
    assert w.login(member["email"]).status_code == 302
    return w


def auth_db():
    """Conexión directa a la base del servicio de cuentas (PostgreSQL embebido de desarrollo)."""
    import psycopg
    import pgserver
    uri = pgserver.get_server(str(ROOT / ".pgdata"), cleanup_mode=None).get_uri(database="auth_db")
    return psycopg.connect(uri, autocommit=True)


@pytest.fixture(scope="session")
def admin_creds():
    """Cuenta administradora: la de QA_ADMIN_EMAIL/QA_ADMIN_PASSWORD o, si no se definen, una temporal creada solo
    para esta ejecución (registro + código del correo + promoción en la base) que se desactiva al terminar."""
    email, password = os.getenv("QA_ADMIN_EMAIL"), os.getenv("QA_ADMIN_PASSWORD")
    if email and password:
        yield email, password
        return
    email = unique_email("qa-admin")
    try:
        web = Web()
        assert web.register(email).status_code == 302 and web.verify(email).status_code == 302
        with auth_db() as conn:
            conn.execute("UPDATE users SET role = 'admin' WHERE email_canonical = %s", (email,))
    except Exception as exc:                                    # noqa: BLE001
        pytest.skip(f"No se pudo crear un administrador temporal ({exc.__class__.__name__}). "
                    "Defina QA_ADMIN_EMAIL y QA_ADMIN_PASSWORD, o inicie el sistema con --mail file")
    yield email, PASSWORD
    try:
        with auth_db() as conn:                                 # la cuenta temporal no debe quedar con permisos
            conn.execute("UPDATE users SET role = 'user', is_active = false WHERE email_canonical = %s", (email,))
    except Exception:                                           # noqa: BLE001
        pass


@pytest.fixture()
def admin_web(admin_creds):
    w = Web()
    resp = w.login(*admin_creds)
    assert resp.status_code == 302, "no se pudo iniciar sesión como administrador"
    return w


def service_token(email: str, password: str) -> str:
    resp = requests.post(f"{AUTH_URL}/api/v1/auth/login", json={"email": email, "password": password}, timeout=15)
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="session")
def admin_token(admin_creds):
    return service_token(*admin_creds)


@pytest.fixture(scope="session")
def member_token(member):
    return service_token(member["email"], member["password"])


def jpeg(size=(900, 600), color=(30, 120, 80)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG")
    return out.getvalue()
