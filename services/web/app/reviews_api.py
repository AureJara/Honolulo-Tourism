"""Pasarela de las opiniones: el navegador solo habla con la web y esta reenvía al catálogo con la sesión del usuario.

La validación de los textos y los permisos los decide el catálogo; aquí solo se comprueba la forma de las rutas, la
cabecera anti-CSRF de las mutaciones y que haya una sesión.
"""

from __future__ import annotations

from flask import Blueprint, Response, g, request

from honolulo_common.errors import ApiError

from .relay import relay
from .security import require_xhr_header
from .upstream import UpstreamUnavailable, anonymous, downstream
from .validators import review_id_or_404, slug_or_404

bp = Blueprint("bff_reviews", __name__)


def _need_session(mutating: bool) -> None:
    if mutating and not require_xhr_header():
        raise ApiError(403, "CSRF_REJECTED", "Solicitud no permitida.")
    if not g.state.access and not g.state.refresh:
        raise ApiError(401, "AUTH_REQUIRED", "Se requiere una sesión activa.")


def _catalog(method: str, path: str, *, session: bool, json=None, params=None) -> Response:
    try:
        if session:
            resp = downstream(g.state, "catalog", method, path, json=json, params=params)
        else:
            resp = anonymous("catalog", method, path, params=params)
    except UpstreamUnavailable:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Las opiniones no están disponibles. Inténtalo nuevamente.") from None
    return relay(resp)


@bp.get("/api/v1/places/<slug>/reviews")
def reviews_list(slug: str):
    """Opiniones públicas de un lugar (las fijadas por el administrador primero)."""
    params = {k: v for k, v in request.args.items() if k in ("limit", "offset")}
    return _catalog("GET", f"/api/v1/places/{slug_or_404(slug)}/reviews", session=False, params=params)


@bp.route("/api/v1/places/<slug>/reviews/mine", methods=["GET", "PUT", "DELETE"])
def my_review(slug: str):
    """Mi opinión sobre el lugar: consultarla, publicarla/editarla o eliminarla (requiere sesión)."""
    _need_session(request.method != "GET")
    body = request.get_json(silent=True) if request.method == "PUT" else None
    return _catalog(request.method, f"/api/v1/places/{slug_or_404(slug)}/reviews/mine", session=True, json=body)


@bp.delete("/api/v1/reviews/<review_id>")
def moderate_delete(review_id: str):
    """El administrador elimina una opinión (el catálogo vuelve a comprobar el rol)."""
    _need_session(True)
    return _catalog("DELETE", f"/api/v1/admin/reviews/{review_id_or_404(review_id)}", session=True)


@bp.put("/api/v1/reviews/<review_id>/pin")
def moderate_pin(review_id: str):
    """El administrador fija o quita de fijadas una opinión."""
    _need_session(True)
    return _catalog("PUT", f"/api/v1/admin/reviews/{review_id_or_404(review_id)}/pin", session=True,
                    json=request.get_json(silent=True))
