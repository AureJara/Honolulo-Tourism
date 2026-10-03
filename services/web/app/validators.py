"""Validación de identificadores que llegan en la URL, antes de llamar a ningún microservicio (respuestas JSON)."""

from __future__ import annotations

import re
import uuid

from honolulo_common.errors import ApiError

_SLUG = re.compile(r"[a-z0-9-]{1,80}")


def slug_or_404(slug: str) -> str:
    if not _SLUG.fullmatch(slug):
        raise ApiError(404, "NOT_FOUND", "Lugar no encontrado.")
    return slug


def review_id_or_404(review_id: str) -> str:
    try:
        return str(uuid.UUID(review_id))
    except ValueError:
        raise ApiError(404, "NOT_FOUND", "Opinión no encontrada.") from None
