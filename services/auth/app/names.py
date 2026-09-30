"""Validación de nombres y apellidos (Escenario 8: datos reales).

No es posible *verificar* que un nombre sea real; se exige un formato plausible y se rechazan
los valores de relleno más comunes. La titularidad del correo se comprueba con el código enviado.
"""

from __future__ import annotations

import re

_ALLOWED_PUNCT = {" ", "'", "’", "-"}
_PLACEHOLDERS = {
    "test", "prueba", "asdf", "qwerty", "nombre", "apellido", "xxx", "xx", "abc", "ninguno", "na", "n/a",
    "usuario", "user", "admin", "null", "none", "fulano", "mengano", "zzz",
}
_REPEAT = re.compile(r"(.)\1{3,}", re.IGNORECASE)        # «aaaa», «xxxx»


class InvalidName(ValueError):
    pass


def clean_person_name(raw: str) -> str:
    """Devuelve el nombre normalizado (espacios colapsados) o lanza ``InvalidName``."""
    value = re.sub(r"\s+", " ", (raw or "").strip())
    if not 2 <= len(value) <= 50:
        raise InvalidName("Debe tener entre 2 y 50 caracteres.")
    if any(not (ch.isalpha() or ch in _ALLOWED_PUNCT) for ch in value):
        raise InvalidName("Solo puede contener letras, espacios, guiones y apóstrofes.")
    if not value[0].isalpha() or not value[-1].isalpha():
        raise InvalidName("Debe empezar y terminar con una letra.")
    if re.search(r"[-'’]{2,}|[-'’] | [-'’]", value):
        raise InvalidName("Los signos deben ir dentro de una palabra.")
    if sum(ch.isalpha() for ch in value) < 2:
        raise InvalidName("Debe tener al menos dos letras.")
    if _REPEAT.search(value) or value.lower() in _PLACEHOLDERS:
        raise InvalidName("Ingresa tu nombre real.")
    return value
