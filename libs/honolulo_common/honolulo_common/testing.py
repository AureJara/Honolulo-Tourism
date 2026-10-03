"""Ayudas para pruebas: PostgreSQL real (nunca SQLite) con una base por ejecución.

Orden de preferencia:
1. ``TEST_DATABASE_URL`` (servidor PostgreSQL existente, p. ej. el de docker compose).
2. PostgreSQL embebido con ``pgserver`` (``pip install pgserver``), útil sin Docker.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

from .weather_provider import ProviderError, Reading

_embedded = None


class FakeProvider:
    """Sustituye al proveedor externo: las pruebas nunca salen a internet."""

    name = "open-meteo"                 # se hace pasar por el proveedor por defecto: así se guardan los datos de prueba

    def __init__(self):
        self.current = None
        self.hourly = []
        self.fail = False
        self.calls = []
        self.last_stats = None

    def fetch_current(self, lat, lon, tz):
        self.calls.append("current")
        if self.fail:
            raise ProviderError("proveedor caído")
        return self.current

    def fetch_hourly(self, lat, lon, tz, *, forecast_days=16, past_days=0):
        self.calls.append(("hourly", past_days))
        if self.fail:
            raise ProviderError("proveedor caído")
        return self.hourly


def search_or_fail(pattern: str, text: str, flags: int = 0) -> re.Match[str]:
    """``re.search`` que falla con un mensaje claro si no hay coincidencia (en vez de un ``AttributeError`` sobre ``None``)."""
    match = re.search(pattern, text, flags)
    assert match, f"no se encontró {pattern!r} en el texto"
    return match


def code_from_message(message) -> str:
    """Código de 6 dígitos del cuerpo de texto de un correo de confirmación (``EmailMessage``)."""
    body = message.get_body(("plain",)).get_content()
    return search_or_fail(r"^\s{2,}(\d{6})\s*$", body, re.M).group(1)


def make_reading(when=None, temp=26.4, code=2, is_day=True, **kw) -> Reading:
    when = when or datetime.now(timezone.utc).replace(second=0, microsecond=0)
    values = dict(time_utc=when, temperature_c=temp, feels_like_c=27.1, humidity_pct=78,
                  precipitation_mm=0.4, rain_probability_pct=35, wind_kph=6.2,
                  cloud_cover_pct=64, weather_code=code, is_day=is_day)
    values.update(kw)
    return Reading(**values)


def _server_url() -> str:
    global _embedded
    env = os.getenv("TEST_DATABASE_URL")
    if env:
        return env
    import pgserver  # import diferido: solo se necesita en desarrollo

    if _embedded is None:
        data_dir = Path(tempfile.gettempdir()) / "honolulo_pg_tests"
        try:
            _embedded = pgserver.get_server(data_dir, cleanup_mode="stop")
        except Exception:                      # noqa: BLE001
            # El directorio temporal puede quedar dañado (p. ej. limpieza de %TEMP% en Windows): es solo
            # un servidor desechable de pruebas, así que se recrea desde cero.
            shutil.rmtree(data_dir, ignore_errors=True)
            _embedded = pgserver.get_server(data_dir, cleanup_mode="stop")
    return _embedded.get_uri()


def create_test_database(prefix: str) -> str:
    """Crea una base vacía y devuelve su URL SQLAlchemy (driver psycopg 3)."""
    base = _server_url().replace("postgresql://", "postgresql+psycopg://", 1)
    plain = base.replace("postgresql+psycopg://", "postgresql://", 1)
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(plain, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    head, _, _ = base.rpartition("/")
    return f"{head}/{name}"


class FakeSmtpServer:
    """Servidor SMTP mínimo en el puerto local para probar el envío real de correo sin salir a Internet.

    Registra los mensajes entregados (``messages``) y las credenciales recibidas (``credentials``). Puede exigir
    autenticación ``AUTH PLAIN`` (``require_auth=(usuario, clave)``) o rechazar el remitente con un código SMTP
    (``fail_with="451 intente luego"``) para simular un servidor caído o que niega el envío.
    """

    def __init__(self, require_auth: tuple[str, str] | None = None, fail_with: str | None = None):
        import socketserver
        import threading

        self.require_auth, self.fail_with = require_auth, fail_with
        self.messages: list[dict] = []
        self.credentials: list[tuple[str, str]] = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):                                  # noqa: D401
                outer._session(self)

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.host, self.port = "127.0.0.1", self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ protocolo
    def _session(self, handler) -> None:
        _SmtpSession(self, handler).run()

    # -------------------------------------------------------------------- uso
    def parsed(self, index: int = -1):
        """Mensaje entregado, decodificado (asunto RFC 2047 y cuerpo quoted-printable)."""
        import email
        import email.policy
        return email.message_from_bytes(self.messages[index]["raw"], policy=email.policy.default)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _SmtpSession:
    """Una conversación SMTP. Cada comando es un método ``do_<VERBO>``: para soportar uno nuevo se añade un método,
    sin tocar el bucle (principio abierto/cerrado)."""

    def __init__(self, server: FakeSmtpServer, handler) -> None:
        self.server, self.handler = server, handler
        self.authed = server.require_auth is None
        self.mail_from, self.rcpt = "", []
        self.done = False

    def say(self, line: str) -> None:
        self.handler.wfile.write((line + "\r\n").encode())
        self.handler.wfile.flush()

    def run(self) -> None:
        self.say("220 fake ESMTP listo")
        while not self.done:
            raw = self.handler.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").strip()
            verb = line.split(" ", 1)[0].upper()
            getattr(self, f"do_{verb}", self.do_unknown)(line)

    def do_unknown(self, line: str) -> None:
        self.say("502 comando no implementado")

    def do_EHLO(self, line: str) -> None:
        if self.server.require_auth is not None:
            self.say("250-fake")
            self.say("250 AUTH PLAIN")
        else:
            self.say("250 fake")

    def do_HELO(self, line: str) -> None:
        self.say("250 fake")

    def do_AUTH(self, line: str) -> None:
        import base64
        try:
            _, user, password = base64.b64decode(line.split()[-1]).decode().split("\0")
        except Exception:                                      # noqa: BLE001
            self.say("501 mal formado")
            return
        self.server.credentials.append((user, password))
        self.authed = self.server.require_auth is not None and (user, password) == self.server.require_auth
        self.say("235 autenticado" if self.authed else "535 credenciales rechazadas")

    def do_MAIL(self, line: str) -> None:
        if not self.authed:
            self.say("530 se requiere autenticación")
        elif self.server.fail_with:
            self.say(self.server.fail_with)
        else:
            self.mail_from, self.rcpt = line.split(":", 1)[1].strip(), []
            self.say("250 ok")

    def do_RCPT(self, line: str) -> None:
        self.rcpt.append(line.split(":", 1)[1].strip().strip("<>"))
        self.say("250 ok")

    def do_DATA(self, line: str) -> None:
        self.say("354 envía los datos")
        chunks = []
        while True:
            part = self.handler.rfile.readline()
            if part in (b".\r\n", b".\n", b""):
                break
            chunks.append(part[1:] if part.startswith(b"..") else part)
        self.server.messages.append({"from": self.mail_from, "to": list(self.rcpt), "raw": b"".join(chunks)})
        self.say("250 en cola")

    def do_RSET(self, line: str) -> None:
        self.say("250 ok")

    do_NOOP = do_RSET

    def do_QUIT(self, line: str) -> None:
        self.say("221 adiós")
        self.done = True

