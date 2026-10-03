"""Administración de lugares (Escenario 6): descripciones, datos de la ruta y fotos.

Solo para cuentas con rol ``admin``. Las páginas son formularios del servidor con protección CSRF;
los cambios se envían al catálogo con la sesión del administrador, que vuelve a comprobar el rol.
"""

from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .admin_common import CATALOG_DOWN, admin_required, catalog_call, error_messages, uuid_or_404
from .admin_reviews import place_reviews
from .security import csrf_token, verify_csrf_form

bp = Blueprint("admin", __name__, url_prefix="/admin")

_DIFFICULTIES = [("", "Sin indicar"), ("easy", "Suave / Fácil"), ("moderate", "Moderada"), ("hard", "Exigente")]
_STATUSES = [("draft", "Borrador (no visible)"), ("published", "Publicado"), ("archived", "Archivado")]


def _optional(form, key, cast):
    raw = form.get(key, "").strip()
    if raw == "":
        return None, None
    try:
        return cast(raw.replace(",", ".") if cast is float else raw), None
    except ValueError:
        return None, "Ingresa un número válido."


def parse_place_form(form) -> tuple[dict, dict]:
    """Convierte el formulario en el JSON del catálogo; los campos vacíos opcionales se envían como null."""
    errors: dict[str, str] = {}
    payload = {"name": form.get("name", "").strip(), "description": form.get("description", "").strip(),
               "status": form.get("status", "draft"), "difficulty": form.get("difficulty") or None,
               "depth_label": form.get("depth_label", "").strip() or None}
    for key, cast in (("hike_minutes", int), ("depth_min_m", float), ("depth_max_m", float)):
        payload[key], err = _optional(form, key, cast)
        if err:
            errors[key] = err
    order, err = _optional(form, "sort_order", int)
    payload["sort_order"] = order if order is not None else 0
    if err:
        errors["sort_order"] = err
    return payload, errors


def _render_form(place, values, errors, *, error=None, status=200, history=None, reviews=None):
    return render_template("admin/place_form.html", place=place, values=values, errors=errors, error=error,
                           history=history or [], reviews=reviews or {"items": [], "total": 0, "summary": None},
                           difficulties=_DIFFICULTIES, statuses=_STATUSES,
                           csrf_token=csrf_token(), user=g.user,
                           max_photo_mb=current_app.config["MAX_PHOTO_BYTES"] // (1024 * 1024)), status


def _values_from(place) -> dict:
    depth = place.get("depth") or {}
    return {"name": place["name"], "description": place["description"],
            "difficulty": (place.get("difficulty") or {}).get("code", ""), "status": place["status"],
            "hike_minutes": (place.get("hike") or {}).get("minutes", ""), "depth_label": depth.get("label", "")
            if depth else "", "depth_min_m": "" if depth.get("min_m") is None else depth.get("min_m"),
            "depth_max_m": "" if depth.get("max_m") is None else depth.get("max_m"), "sort_order": place["sort_order"]}


def _fetch_place(place_id: str) -> dict:
    resp = catalog_call("GET", f"/api/v1/admin/places/{uuid_or_404(place_id)}")
    if resp.status_code == 404:
        abort(404)
    if resp.status_code == 403:
        abort(403, description="No tienes permisos para administrar los lugares.")
    return resp.json()


# ------------------------------------------------------------------- lugares
@bp.get("/lugares")
@admin_required
def places():
    resp = catalog_call("GET", "/api/v1/admin/places")
    if resp.status_code != 200:
        abort(503, description=CATALOG_DOWN)
    return render_template("admin/places.html", places=resp.json()["items"], csrf_token=csrf_token(), user=g.user)


@bp.route("/lugares/nuevo", methods=["GET", "POST"])
@admin_required
def place_new():
    if request.method == "GET":
        return _render_form(None, {"status": "draft", "sort_order": 0}, {})
    verify_csrf_form()
    payload, errors = parse_place_form(request.form)
    if errors:
        return _render_form(None, request.form, errors, status=422)
    resp = catalog_call("POST", "/api/v1/admin/places", json=payload)
    if resp.status_code == 201:
        flash("Lugar creado. Ahora puedes subir sus fotos.", "ok")
        return redirect(url_for("admin.place_edit", place_id=resp.json()["id"]))
    fields, message = error_messages(resp)
    return _render_form(None, request.form, fields, error=message, status=resp.status_code if resp.status_code in (422, 403) else 400)


@bp.route("/lugares/<place_id>", methods=["GET", "POST"])
@admin_required
def place_edit(place_id: str):
    place = _fetch_place(place_id)
    if request.method == "GET":
        hist = catalog_call("GET", f"/api/v1/admin/places/{place['id']}/history")
        return _render_form(place, _values_from(place), {}, history=hist.json().get("items", [])[:10] if hist.ok else [],
                            reviews=place_reviews(place))

    verify_csrf_form()
    payload, errors = parse_place_form(request.form)
    if errors:
        return _render_form(place, request.form, errors, status=422)
    resp = catalog_call("PATCH", f"/api/v1/admin/places/{place['id']}", json=payload)
    if resp.status_code == 200:
        flash("Cambios guardados.", "ok")
        return redirect(url_for("admin.place_edit", place_id=place["id"]))
    fields, message = error_messages(resp)
    return _render_form(place, request.form, fields, error=message, status=resp.status_code if resp.status_code in (422, 403) else 400)


# --------------------------------------------------------------------- fotos
def _back(place_id: str):
    return redirect(url_for("admin.place_edit", place_id=uuid_or_404(place_id)))


@bp.post("/lugares/<place_id>/fotos")
@admin_required
def photo_upload(place_id: str):
    verify_csrf_form()
    place_id = uuid_or_404(place_id)
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("Elige una foto para subir.", "error")
        return _back(place_id)
    data = upload.stream.read(current_app.config["MAX_PHOTO_BYTES"] + 1)
    resp = catalog_call("POST", f"/api/v1/admin/places/{place_id}/photos",
                 files={"file": (upload.filename, data, "application/octet-stream")},
                 data={"alt_text": request.form.get("alt_text", ""),
                       "is_cover": "true" if request.form.get("is_cover") == "on" else "false"})
    if resp.status_code == 201:
        flash("Foto subida.", "ok")
    else:
        fields, message = error_messages(resp)
        flash(message or next(iter(fields.values()), "No se pudo subir la foto."), "error")
    return _back(place_id)


def _photo_action(photo_id: str, method: str, **kwargs):
    place_id = request.form.get("place_id", "")
    resp = catalog_call(method, f"/api/v1/admin/photos/{uuid_or_404(photo_id)}", **kwargs)
    return place_id, resp


@bp.post("/fotos/<photo_id>/portada")
@admin_required
def photo_cover(photo_id: str):
    verify_csrf_form()
    place_id, resp = _photo_action(photo_id, "PATCH", json={"is_cover": True})
    flash("Portada actualizada." if resp.ok else error_messages(resp)[1] or "No se pudo cambiar la portada.",
          "ok" if resp.ok else "error")
    return _back(place_id)


@bp.post("/fotos/<photo_id>/texto")
@admin_required
def photo_alt(photo_id: str):
    verify_csrf_form()
    place_id, resp = _photo_action(photo_id, "PATCH", json={"alt_text": request.form.get("alt_text", "").strip()})
    if resp.ok:
        flash("Texto alternativo actualizado.", "ok")
    else:
        fields, message = error_messages(resp)
        flash(fields.get("alt_text") or message or "No se pudo actualizar el texto.", "error")
    return _back(place_id)


@bp.post("/fotos/<photo_id>/eliminar")
@admin_required
def photo_delete(photo_id: str):
    verify_csrf_form()
    place_id, resp = _photo_action(photo_id, "DELETE")
    flash("Foto eliminada." if resp.status_code == 204 else "No se pudo eliminar la foto.",
          "ok" if resp.status_code == 204 else "error")
    return _back(place_id)
