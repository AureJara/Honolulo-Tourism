"""Envío de correo. Backends: ``smtp`` (producción, p. ej. Gmail con contraseña de aplicación),
``file`` (desarrollo/QA: deja el mensaje en una carpeta) y ``memory`` (pruebas)."""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)


class MailError(Exception):
    """No se pudo entregar el correo al servidor de salida."""


def build_verification_message(to: str, first_name: str, code: str, ttl_minutes: int) -> EmailMessage:
    cfg = current_app.config
    msg = EmailMessage()
    msg["Subject"] = f"Tu código de confirmación de Honolulo: {code}"
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = to
    msg.set_content(
        f"Hola {first_name},\n\n"
        f"Tu código de confirmación para Honolulo es:\n\n    {code}\n\n"
        f"Vence en {ttl_minutes} minutos. Si no creaste una cuenta en Honolulo, ignora este mensaje: "
        "nadie podrá usar tu correo sin este código.\n\n"
        "Por tu seguridad, Honolulo nunca te pedirá este código por teléfono o por chat.\n")
    msg.add_alternative(
        f"<p>Hola {_html(first_name)},</p><p>Tu código de confirmación para Honolulo es:</p>"
        f"<p style=\"font-size:28px;font-weight:700;letter-spacing:6px\">{code}</p>"
        f"<p>Vence en {ttl_minutes} minutos. Si no creaste una cuenta en Honolulo, ignora este mensaje.</p>"
        "<p style=\"color:#666\">Honolulo nunca te pedirá este código por teléfono o por chat.</p>",
        subtype="html")
    return msg


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send(message: EmailMessage) -> None:
    backend = current_app.config["MAIL_BACKEND"]
    if backend == "memory":
        current_app.extensions.setdefault("mail_outbox", []).append(message)
    elif backend == "file":
        _send_file(message)
    elif backend == "smtp":
        _send_smtp(message)
    else:
        raise MailError(f"MAIL_BACKEND desconocido: {backend}")


def _send_file(message: EmailMessage) -> None:
    outbox = Path(current_app.config["MAIL_OUTBOX_DIR"])
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe = re.sub(r"[^A-Za-z0-9@._-]", "_", message["To"])
    (outbox / f"{stamp}-{safe}.txt").write_text(message.as_string(), encoding="utf-8")


def _send_smtp(message: EmailMessage) -> None:
    cfg = current_app.config
    host, security = cfg["SMTP_HOST"], cfg["SMTP_SECURITY"]
    if not host:
        raise MailError("SMTP_HOST no está configurado")
    try:
        if security == "ssl":
            client = smtplib.SMTP_SSL(host, cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT_S"],
                                      context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(host, cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT_S"])
        with client:
            if security == "starttls":
                client.starttls(context=ssl.create_default_context())
            if cfg["SMTP_USER"]:
                client.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Fallo SMTP (%s): %s", host, exc)
        raise MailError(str(exc)) from exc


def send_verification_email(to: str, first_name: str, code: str) -> None:
    ttl = current_app.config["VERIFICATION_CODE_TTL_MIN"]
    send(build_verification_message(to, first_name, code, ttl))
