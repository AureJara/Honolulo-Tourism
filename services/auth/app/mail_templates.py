"""Contenido de los correos. Solo el texto y su formato: cómo se entregan lo decide ``mailer``."""

from __future__ import annotations

from email.message import EmailMessage

from flask import current_app


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_verification_message(to: str, first_name: str, code: str, ttl_minutes: int) -> EmailMessage:
    """Correo con el código de confirmación (texto plano + HTML)."""
    msg = EmailMessage()
    msg["Subject"] = f"Tu código de confirmación de Honolulo: {code}"
    msg["From"] = current_app.config["MAIL_FROM"]
    msg["To"] = to
    msg.set_content(
        f"Hola {first_name},\n\n"
        f"Tu código de confirmación para Honolulo es:\n\n    {code}\n\n"
        f"Vence en {ttl_minutes} minutos. Si no creaste una cuenta en Honolulo, ignora este mensaje: "
        "nadie podrá usar tu correo sin este código.\n\n"
        "Por tu seguridad, Honolulo nunca te pedirá este código por teléfono o por chat.\n")
    msg.add_alternative(
        f"<p>Hola {escape_html(first_name)},</p><p>Tu código de confirmación para Honolulo es:</p>"
        f"<p style=\"font-size:28px;font-weight:700;letter-spacing:6px\">{code}</p>"
        f"<p>Vence en {ttl_minutes} minutos. Si no creaste una cuenta en Honolulo, ignora este mensaje.</p>"
        "<p style=\"color:#666\">Honolulo nunca te pedirá este código por teléfono o por chat.</p>",
        subtype="html")
    return msg
