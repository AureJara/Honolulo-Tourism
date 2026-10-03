"""Política de contraseñas: longitud, no triviales y que no contengan datos personales.

Cada regla es una función registrada con ``@password_rule``; ``check_password`` las aplica en el orden en que se
definieron y devuelve el primer motivo de rechazo. Principio abierto/cerrado: añadir una regla nueva (p. ej.
consultar una lista de claves filtradas) es escribir otra función, sin tocar las existentes ni ``check_password``.

Solo el usuario conoce su contraseña: aquí nunca se registra ni se devuelve su valor, y en la base de datos
únicamente queda el hash con sal (scrypt).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

MAX_LENGTH = 128

# Contraseñas muy usadas (en minúsculas). Se compara también sin los números/símbolos finales: «clave2026» ≈ «clave».
_COMMON = frozenset("""
password contraseña contrasena passw0rd p@ssw0rd pass clave claves secreto secret admin administrador administrator
root user usuario login welcome bienvenido bienvenida hola holamundo hello letmein iloveyou teamo tequiero tuamo amor
princesa princess dragon monkey shadow master sunshine superman batman football futbol soccer baseball qwerty
qwertyuiop qwerty123 asdf asdfgh asdfghjkl zxcvbnm qazwsx zaq12wsx abc123 abcdef abcdefgh trustno1 1q2w3e4r
honolulo tingomaria tingo maria peru lima huanuco cascada cascadas catarata cataratas selva marulla
""".split())
_SEQUENCES = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiopasdfghjklzxcvbnm")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _personal_tokens(personal: tuple[str, ...]) -> frozenset[str]:
    tokens: set[str] = set()
    for item in personal:
        for part in re.split(r"[^a-z0-9]+", _fold(item or "")):
            if len(part) >= 4:
                tokens.add(part)
    return frozenset(tokens)


@dataclass(frozen=True)
class Candidate:
    """La contraseña a evaluar y lo que las reglas necesitan saber de ella."""

    password: str
    min_len: int
    personal: frozenset[str]            # fragmentos del nombre, apellido y correo (ya normalizados)

    @property
    def folded(self) -> str:
        return _fold(self.password)


PasswordRule = Callable[[Candidate], "str | None"]       # devuelve el motivo del rechazo, o None si la acepta
_RULES: list[PasswordRule] = []


def password_rule(rule: PasswordRule) -> PasswordRule:
    """Registra una regla. Se aplican en el orden de registro."""
    _RULES.append(rule)
    return rule


@password_rule
def _length(c: Candidate) -> str | None:
    if len(c.password) < c.min_len or len(c.password) > MAX_LENGTH:
        return f"Debe tener entre {c.min_len} y {MAX_LENGTH} caracteres."
    return None


@password_rule
def _letters_and_numbers(c: Candidate) -> str | None:
    if c.password.isdigit() or c.password.isalpha():
        return "Debe combinar letras y números."
    return None


@password_rule
def _not_common_or_predictable(c: Candidate) -> str | None:
    folded = c.folded
    base = re.sub(r"[\d\W_]+$", "", folded)                       # sin números/símbolos al final
    letters = re.sub(r"[^a-z0-9]", "", folded)
    sequential = len(letters) >= 8 and any(letters in seq or letters in seq[::-1] for seq in _SEQUENCES)
    if folded in _COMMON or base in _COMMON or sequential:
        return "Es una contraseña demasiado común o predecible. Elige otra."
    return None


@password_rule
def _enough_variety(c: Candidate) -> str | None:
    return "Usa más variedad de caracteres." if len(set(c.folded)) < 5 else None


@password_rule
def _no_personal_data(c: Candidate) -> str | None:
    if any(token in c.folded for token in c.personal):
        return "No debe contener tu nombre, tu apellido ni parte de tu correo."
    return None


def check_password(password: str, *, min_len: int, personal: tuple[str, ...] = ()) -> str | None:
    """Devuelve el motivo del rechazo, o ``None`` si la contraseña es aceptable.

    ``personal`` son datos de la propia persona (nombre, apellido, correo) que no deben formar parte de la clave.
    """
    candidate = Candidate(password, min_len, _personal_tokens(personal))
    for rule in _RULES:
        problem = rule(candidate)
        if problem:
            return problem
    return None
