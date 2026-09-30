"""Pasarela (BFF) de la API: reenvía ``/api/v1/weather/*`` al microservicio correspondiente con la
sesión del usuario. El contrato público sigue siendo el del spec (``/api/v1/weather/current`` etc.)."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, Response, g, jsonify, request

from honolulo_common.errors import ApiError

from .security import require_xhr_header
from .upstream import UpstreamUnavailable, anonymous, downstream

bp = Blueprint("bff", __name__)

RN08_MESSAGE = "No se pudo actualizar la información meteorológica. Inténtelo nuevamente."
UNAVAILABLE_MESSAGE = "El servicio meteorológico no está disponible. Inténtelo nuevamente."

# primer segmento de la ruta → (servicio, métodos permitidos)
_ROUTES = {
    "current": ("weather", {"GET"}), "history": ("weather", {"GET"}),
    "status": ("weather", {"GET"}), "location": ("weather", {"GET"}),
    "forecast": ("forecast", {"GET", "POST"}), "calendar": ("forecast", {"GET"}),
    "evaluation": ("forecast", {"GET"}), "scoring-parameters": ("forecast", {"GET", "PUT"}),
    "refresh": (None, {"POST"}),                     # combinado: clima actual + pronóstico
}
_POST_ONLY = {"forecast/refresh"}


def _unavailable() -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE", UNAVAILABLE_MESSAGE)


def _relay(resp) -> Response:
    out = Response(resp.content, status=resp.status_code,
                   content_type=resp.headers.get("Content-Type", "application/json"))
    out.headers["Cache-Control"] = "no-store"
    if "Retry-After" in resp.headers:
        out.headers["Retry-After"] = resp.headers["Retry-After"]
    if resp.status_code == 401:
        g.state.cleared = True            # la sesión ya no sirve: se limpian las cookies del navegador
    return out


def _json(resp):
    try:
        return resp.json()
    except ValueError:
        return None


@bp.route("/api/v1/weather/<path:rest>", methods=["GET", "POST", "PUT"])
def weather_api(rest: str):
    first = rest.split("/", 1)[0]
    if first not in _ROUTES:
        raise ApiError(404, "NOT_FOUND", "Recurso no encontrado.")
    service, methods = _ROUTES[first]
    if request.method not in methods or (request.method == "POST" and first == "forecast" and rest not in _POST_ONLY):
        raise ApiError(405, "METHOD_NOT_ALLOWED", "Método no permitido.")
    if request.method != "GET" and not require_xhr_header():
        raise ApiError(403, "CSRF_REJECTED", "Solicitud no permitida.")
    state = g.state
    if not state.access and not state.refresh:
        raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.")

    if first == "refresh":
        return _combined_refresh()
    body = request.get_json(silent=True) if request.method == "PUT" else None
    try:
        resp = downstream(state, service, request.method, f"/api/v1/weather/{rest}",
                          params=request.args.to_dict(flat=True), json=body)
    except UpstreamUnavailable:
        raise _unavailable() from None
    return _relay(resp)


@bp.get("/api/v1/auth/me")
def me():
    if not g.state.access and not g.state.refresh:
        raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.")
    try:
        return _relay(downstream(g.state, "auth", "GET", "/api/v1/auth/me"))
    except UpstreamUnavailable:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "El servicio de autenticación no está disponible.") from None


def _combined_refresh():
    """RF07 «Actualizar clima»: actualiza el clima actual y los pronósticos en paralelo."""
    state = g.state

    def call(service: str, path: str):
        try:
            return downstream(state, service, "POST", path)
        except UpstreamUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        current_f = pool.submit(call, "weather", "/api/v1/weather/refresh")
        forecast_f = pool.submit(call, "forecast", "/api/v1/weather/forecast/refresh")
        current, forecast = current_f.result(), forecast_f.result()

    statuses = [r.status_code if r is not None else None for r in (current, forecast)]
    if 401 in statuses:
        state.cleared = True
        raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.")

    cur_body = _json(current) if current is not None else None
    fc_body = _json(forecast) if forecast is not None else None
    cur_ok, fc_ok = statuses[0] == 200, statuses[1] == 200
    # Si el proveedor falló, el servicio devuelve el último dato guardado marcado como anterior.
    shown = cur_body if cur_ok else (cur_body or {}).get("last_known")

    if not (cur_ok or fc_ok):
        if all(s is None for s in statuses):
            raise _unavailable()
        raise ApiError(502, "WEATHER_PROVIDER_UNAVAILABLE", RN08_MESSAGE,
                       extra={"last_known": shown} if shown else None)

    payload = {
        "refreshed": bool((cur_ok and cur_body.get("refreshed")) or (fc_ok and fc_body.get("refreshed"))),
        "current": shown,
        "forecast": fc_body if fc_ok else None,
    }
    if not (cur_ok and fc_ok):
        payload["message"] = RN08_MESSAGE
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------ lugares y fotos
_MEDIA_KEY = re.compile(r"^[0-9a-f]{32}-(1600|640)\.webp$")


@bp.get("/api/v1/places")
def places_list():
    return _public_catalog("/api/v1/places")


@bp.get("/api/v1/places/<slug>")
def places_detail(slug: str):
    if not re.fullmatch(r"[a-z0-9-]{1,80}", slug):
        raise ApiError(404, "NOT_FOUND", "Lugar no encontrado.")
    return _public_catalog(f"/api/v1/places/{slug}")


def _public_catalog(path: str) -> Response:
    try:
        resp = anonymous("catalog", "GET", path)
    except UpstreamUnavailable:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "El catálogo de lugares no está disponible. Inténtelo nuevamente.") from None
    out = Response(resp.content, status=resp.status_code,
                   content_type=resp.headers.get("Content-Type", "application/json"))
    out.headers["Cache-Control"] = "public, max-age=30" if resp.status_code == 200 else "no-store"
    return out


@bp.get("/media/<key>")
def media(key: str):
    """Fotos de los lugares: se sirven desde el catálogo con caché larga (la clave es inmutable)."""
    if not _MEDIA_KEY.match(key):
        raise ApiError(404, "NOT_FOUND", "Archivo no encontrado.")
    headers = {}
    if request.headers.get("If-None-Match"):
        headers["If-None-Match"] = request.headers["If-None-Match"]
    try:
        resp = anonymous("catalog", "GET", f"/media/{key}", headers=headers)
    except UpstreamUnavailable:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Las fotos no están disponibles en este momento.") from None
    if resp.status_code not in (200, 304):
        raise ApiError(404, "NOT_FOUND", "Archivo no encontrado.")
    out = Response(resp.content if resp.status_code == 200 else b"", status=resp.status_code,
                   content_type="image/webp")
    out.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    out.headers["X-Content-Type-Options"] = "nosniff"
    out.headers["Content-Security-Policy"] = "default-src 'none'"
    if "ETag" in resp.headers:
        out.headers["ETag"] = resp.headers["ETag"]
    return out
