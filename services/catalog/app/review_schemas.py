"""Validación y limpieza de lo que escribe una persona al opinar.

El comentario pasa por dos listas ampliables (principio abierto/cerrado):

* *saneadores* (``@comment_sanitizer``): transforman el texto (quitar caracteres invisibles, espacios…);
* *reglas* (``@comment_rule``): lo aceptan (``None``) o devuelven el motivo del rechazo.

Añadir una norma nueva (p. ej. un filtro de insultos) es escribir una función con el decorador; ``normalize_comment``
y ``load_review`` no se modifican.
"""

from __future__ import annotations

import re
from typing import Callable

from flask import request
from marshmallow import ValidationError, fields, validate

from honolulo_common.errors import ApiError, validation_error

from .schemas import BaseSchema
from .text import clean_text

MIN_COMMENT = 3
_RAW_LIMIT_FACTOR = 3            # un texto de más de 3 veces el máximo ni se analiza (evita trabajo inútil)
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")   # ceros de ancho, RLO/LRO…
_LINK = re.compile(
    r"(?i)(?:https?://|ftp://|www\.|\b[a-z0-9][a-z0-9-]*\.(?:com|net|org|pe|io|me|ly|xyz|info|link|app|co)\b)")

Sanitizer = Callable[[str], str]
CommentRule = Callable[[str, int], "str | None"]          # (texto limpio, máximo de caracteres) -> motivo o None
_SANITIZERS: list[Sanitizer] = []
_RULES: list[CommentRule] = []


def comment_sanitizer(func: Sanitizer) -> Sanitizer:
    _SANITIZERS.append(func)
    return func


def comment_rule(func: CommentRule) -> CommentRule:
    _RULES.append(func)
    return func


@comment_sanitizer
def _strip_invisible(text: str) -> str:
    return _INVISIBLE.sub("", text)


@comment_sanitizer
def _clean_whitespace(text: str) -> str:
    return clean_text(text, multiline=True)


@comment_sanitizer
def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


@comment_rule
def _long_enough(text: str, max_chars: int) -> str | None:
    return None if len(text) >= MIN_COMMENT else f"Escribe al menos {MIN_COMMENT} caracteres o deja el comentario vacío."


@comment_rule
def _short_enough(text: str, max_chars: int) -> str | None:
    return None if len(text) <= max_chars else f"El comentario admite hasta {max_chars} caracteres."


@comment_rule
def _no_links(text: str, max_chars: int) -> str | None:
    return "Por seguridad no se permiten enlaces en los comentarios." if _LINK.search(text) else None


def normalize_comment(raw: str | None, max_chars: int) -> str | None:
    """Comentario listo para guardarse: saneado, y ``None`` si quedó vacío. Lanza un 422 si incumple una regla."""
    if raw is None:
        return None
    if len(raw) > max_chars * _RAW_LIMIT_FACTOR:
        raise validation_error({"comment": [f"El comentario admite hasta {max_chars} caracteres."]})
    text = raw
    for sanitize in _SANITIZERS:
        text = sanitize(text)
    if not text:
        return None
    for rule in _RULES:
        problem = rule(text, max_chars)
        if problem:
            raise validation_error({"comment": [problem]})
    return text


# ------------------------------------------------------------------- esquemas
class ReviewSchema(BaseSchema):
    rating = fields.Integer(required=True, strict=True, validate=validate.Range(min=1, max=5))
    comment = fields.String(allow_none=True)


def _is_bool(value) -> None:
    if not isinstance(value, bool):                 # 1 y "true" no cuentan: evita ambigüedades
        raise ValidationError("Debe ser verdadero o falso.")


class PinSchema(BaseSchema):
    pinned = fields.Raw(required=True, validate=_is_bool)


def load_review(max_chars: int) -> dict:
    """Valida el cuerpo de una opinión: puntuación entera 1–5 y comentario opcional que cumpla las reglas."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(400, "BAD_REQUEST", "Se esperaba un cuerpo JSON.")
    if isinstance(payload.get("rating"), bool):            # True es un entero en Python: no es una puntuación
        raise validation_error({"rating": ["Elige de 1 a 5 estrellas."]})
    try:
        data = ReviewSchema().load(payload)
    except ValidationError as exc:
        messages = exc.normalized_messages()
        if "rating" in messages:
            messages["rating"] = ["Elige de 1 a 5 estrellas."]
        raise validation_error(messages) from exc
    return {"rating": data["rating"], "comment": normalize_comment(data.get("comment"), max_chars)}
