"""Moderación de opiniones desde la administración: fijar y eliminar comentarios de los usuarios.

Solo para cuentas con rol ``admin`` (el catálogo vuelve a comprobar el rol). Son formularios del servidor con CSRF.
"""

from __future__ import annotations

from flask import Blueprint, flash, g, redirect, request, url_for

from .admin_common import admin_required, catalog_call, error_messages, uuid_or_404
from .security import verify_csrf_form
from .upstream import UpstreamUnavailable, downstream

bp = Blueprint("admin_reviews", __name__, url_prefix="/admin")


def place_reviews(place: dict) -> dict | None:
    """Opiniones del lugar para moderarlas (un lugar en borrador aún no las tiene públicas)."""
    try:
        resp = downstream(g.state, "catalog", "GET", f"/api/v1/places/{place['slug']}/reviews", params={"limit": 50})
    except UpstreamUnavailable:
        return None                                  # la edición del lugar no depende de las opiniones
    return resp.json() if resp.status_code == 200 else None


def _back_to_reviews(place_id: str):
    return redirect(url_for("admin.place_edit", place_id=uuid_or_404(place_id)) + "#opiniones")


@bp.post("/opiniones/<review_id>/eliminar")
@admin_required
def review_delete(review_id: str):
    verify_csrf_form()
    resp = catalog_call("DELETE", f"/api/v1/admin/reviews/{uuid_or_404(review_id)}")
    if resp.status_code == 204:
        flash("Opinión eliminada.", "ok")
    elif resp.status_code == 404:
        flash("Esa opinión ya no existe.", "info")
    else:
        flash(error_messages(resp)[1] or "No se pudo eliminar la opinión.", "error")
    return _back_to_reviews(request.form.get("place_id", ""))


@bp.post("/opiniones/<review_id>/fijar")
@admin_required
def review_pin(review_id: str):
    verify_csrf_form()
    pinned = request.form.get("pinned") == "1"
    resp = catalog_call("PUT", f"/api/v1/admin/reviews/{uuid_or_404(review_id)}/pin", json={"pinned": pinned})
    if resp.status_code == 200:
        flash("Opinión fijada: aparecerá primero." if pinned else "Se quitó la opinión de las fijadas.", "ok")
    elif resp.status_code == 404:
        flash("Esa opinión ya no existe.", "info")
    else:
        flash(error_messages(resp)[1] or "No se pudo cambiar la opinión.", "error")
    return _back_to_reviews(request.form.get("place_id", ""))
