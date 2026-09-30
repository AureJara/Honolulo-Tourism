"""Normalización de correos. Gmail ignora los puntos y todo lo que sigue a «+» en la parte local,
así que ``a.b+x@gmail.com`` y ``ab@gmail.com`` son el mismo buzón: no deben crear dos cuentas."""

from __future__ import annotations

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def normalize_email(email: str) -> str:
    return email.strip().lower()


def canonical_email(email: str) -> str:
    email = normalize_email(email)
    local, _, domain = email.rpartition("@")
    if domain in _GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"
