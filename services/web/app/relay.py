"""Reenvío de respuestas de los microservicios al navegador (pasarela)."""

from __future__ import annotations

from flask import Response, g


def relay(resp) -> Response:
    """Devuelve tal cual la respuesta del servicio, sin caché; si la sesión ya no sirve, marca las cookies para borrarse."""
    out = Response(resp.content, status=resp.status_code,
                   content_type=resp.headers.get("Content-Type", "application/json"))
    out.headers["Cache-Control"] = "no-store"
    if "Retry-After" in resp.headers:
        out.headers["Retry-After"] = resp.headers["Retry-After"]
    if resp.status_code == 401:
        g.state.cleared = True            # la sesión ya no sirve: se limpian las cookies del navegador
    return out


def json_or_none(resp):
    try:
        return resp.json()
    except ValueError:
        return None
