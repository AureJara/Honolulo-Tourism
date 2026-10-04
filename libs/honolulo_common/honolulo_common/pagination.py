"""Parámetros de paginación (``limit`` y ``offset``) de las listas de la API, validados de la misma forma en todos los servicios."""

from __future__ import annotations

from flask import request

from .errors import ApiError


def page_args(*, default_limit: int = 20, max_limit: int = 50, max_offset: int = 100_000) -> tuple[int, int]:
    """``limit`` (1 a ``max_limit``) y ``offset`` (0 a ``max_offset``) de la petición, o un 422 que explica qué falló."""
    errors, values = [], {}
    for key, low, high, fallback in (("limit", 1, max_limit, default_limit), ("offset", 0, max_offset, 0)):
        raw = request.args.get(key)
        try:
            number = fallback if raw is None else int(raw)
        except ValueError:
            number = None
        if number is None or not low <= number <= high:
            errors.append({"field": key, "message": f"Debe ser un entero entre {low} y {high}."})
        values[key] = number
    if errors:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los parámetros.", errors=errors)
    return values["limit"], values["offset"]
