"""Limpieza de texto escrito por personas (compartida por lugares, fotos y opiniones)."""

from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")      # conserva \n y \t


def clean_text(value: str, *, multiline: bool = False) -> str:
    """Quita caracteres de control y espacios sobrantes; en una línea además colapsa los espacios internos."""
    value = _CONTROL.sub("", value).strip()
    if not multiline:
        value = re.sub(r"\s+", " ", value)
    return value
