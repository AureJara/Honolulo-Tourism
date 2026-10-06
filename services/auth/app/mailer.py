"""Entrega de correo. Cada forma de enviar es un *backend* que se registra con ``@register_backend``:

* ``smtp``   producción (p. ej. Gmail con contraseña de aplicación): sale a Internet;
* ``file``   desarrollo/QA: deja el mensaje en una carpeta;
* ``memory`` pruebas.

Principio abierto/cerrado: para añadir otro (un proveedor por API, una cola…) se escribe una función nueva con el
decorador; ``send`` y quienes lo usan no se modifican. Lo mismo con las formas de abrir la conexión SMTP
(``@register_smtp_security``). El texto de los mensajes vive en ``mail_templates``.
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

from flask import current_app

from .mail_templates import build_verification_message

log = logging.getLogger(__name__)


class MailError(Exception):
    """No se pudo entregar el correo al servidor de salida."""


# ------------------------------------------------------------ backends de entrega
@dataclass(frozen=True)
class Backend:
    send: Callable[[EmailMessage], None]
    external: bool                                   # ¿el mensaje sale a Internet?
    describe: Callable[[], str] = lambda: ""         # dónde queda el mensaje (solo informativo)


_BACKENDS: dict[str, Backend] = {}


def register_backend(name: str, *, external: bool, describe: Callable[[], str] | None = None):
    """Registra una forma de enviar correo bajo el nombre que se usa en ``MAIL_BACKEND``."""
    def decorator(func: Callable[[EmailMessage], None]) -> Callable[[EmailMessage], None]:
        _BACKENDS[name] = Backend(func, external, describe or (lambda: ""))
        return func
    return decorator


def backend(name: str) -> Backend:
    try:
        return _BACKENDS[name]
    except KeyError:
        raise MailError(f"MAIL_BACKEND desconocido: {name}") from None


def send(message: EmailMessage) -> None:
    backend(current_app.config["MAIL_BACKEND"]).send(message)


def delivery_info() -> dict:
    """Cómo sale el correo con la configuración actual, para que la web no afirme «te enviamos un correo» cuando el
    mensaje se queda en una carpeta (desarrollo). En producción el envío es siempre externo (ver ``create_app``): ahí solo
    se informa ``external: true`` y no se revela nada de la configuración."""
    try:
        chosen = backend(current_app.config["MAIL_BACKEND"])
    except MailError:
        return {"external": False, "where": ""}
    return {"external": True} if chosen.external else {"external": False, "where": chosen.describe()}


@register_backend("memory", external=False)
def _send_memory(message: EmailMessage) -> None:
    current_app.extensions.setdefault("mail_outbox", []).append(message)


def _file_destination() -> str:
    return f"la carpeta {current_app.config['MAIL_OUTBOX_DIR']}"


@register_backend("file", external=False, describe=_file_destination)
def _send_file(message: EmailMessage) -> None:
    outbox = Path(current_app.config["MAIL_OUTBOX_DIR"])
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe = re.sub(r"[^A-Za-z0-9@._-]", "_", message["To"])
    (outbox / f"{stamp}-{safe}.txt").write_text(message.as_string(), encoding="utf-8")


# ------------------------------------------------------ SMTP: formas de conectar
_SMTP_SECURITY: dict[str, Callable[[dict], smtplib.SMTP]] = {}


def register_smtp_security(name: str):
    """Registra una forma de abrir la conexión SMTP (valor de ``SMTP_SECURITY``): devuelve el cliente ya conectado."""
    def decorator(func: Callable[[dict], smtplib.SMTP]) -> Callable[[dict], smtplib.SMTP]:
        _SMTP_SECURITY[name] = func
        return func
    return decorator


@register_smtp_security("ssl")
def _connect_ssl(cfg: dict) -> smtplib.SMTP:
    return smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT_S"],
                            context=ssl.create_default_context())


@register_smtp_security("starttls")
def _connect_starttls(cfg: dict) -> smtplib.SMTP:
    client = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT_S"])
    try:
        client.starttls(context=ssl.create_default_context())
    except Exception:
        client.close()
        raise
    return client


@register_smtp_security("none")
def _connect_plain(cfg: dict) -> smtplib.SMTP:
    return smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT_S"])


@register_backend("smtp", external=True)
def _send_smtp(message: EmailMessage) -> None:
    cfg = current_app.config
    if not cfg["SMTP_HOST"]:
        raise MailError("SMTP_HOST no está configurado")
    connect = _SMTP_SECURITY.get(cfg["SMTP_SECURITY"])
    if connect is None:                       # un valor mal escrito nunca debe degradar la conexión a texto plano
        raise MailError(f"SMTP_SECURITY desconocido: {cfg['SMTP_SECURITY']}")
    try:
        with connect(cfg) as client:
            if cfg["SMTP_USER"]:
                client.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Fallo SMTP (%s): %s", cfg["SMTP_HOST"], exc)
        raise MailError(str(exc)) from exc


# ----------------------------------------------------------------- casos de uso
def send_verification_email(to: str, first_name: str, code: str) -> None:
    ttl = current_app.config["VERIFICATION_CODE_TTL_MIN"]
    send(build_verification_message(to, first_name, code, ttl))
