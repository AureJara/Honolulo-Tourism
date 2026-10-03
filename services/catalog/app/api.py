from __future__ import annotations

import uuid

from flask import Blueprint, abort, current_app, jsonify, request, send_file

from honolulo_common.errors import ApiError

from . import service
from .permissions import admin_only
from .responses import no_store, public_cache
from .reviews import rating_summaries
from .schemas import PhotoUpdateSchema, PlaceCreateSchema, PlaceFields, load_body
from .text import clean_text

bp = Blueprint("catalog", __name__)


# ------------------------------------------------------------ API pública
@bp.get("/api/v1/places")
def list_places():
    places = service.list_places(published_only=True)
    ratings = rating_summaries([p.id for p in places])
    items = [service.serialize_place(p, ratings=ratings) for p in places]
    return public_cache(jsonify({"items": items, "total": len(items)}))


@bp.get("/api/v1/places/<slug>")
def get_place(slug: str):
    return public_cache(jsonify(service.serialize_place(service.get_public(slug))))


@bp.get("/media/<key>")
def media(key: str):
    storage = service.storage()
    if not storage.exists(key):
        abort(404)
    resp = send_file(storage.path(key), mimetype="image/webp", conditional=True)
    # La clave lleva el UUID de la foto: el contenido de una clave nunca cambia.
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'"
    return resp


# ------------------------------------------------ administración (Escenario 6)
@bp.get("/api/v1/admin/places")
@admin_only
def admin_list():
    places = service.list_places(published_only=False)
    ratings = rating_summaries([p.id for p in places])
    items = [service.serialize_place(p, admin=True, ratings=ratings) for p in places]
    return no_store(jsonify({"items": items, "total": len(items)}))


@bp.post("/api/v1/admin/places")
@admin_only
def admin_create():
    place = service.create_place(load_body(PlaceCreateSchema()))
    return no_store(jsonify(service.serialize_place(place, admin=True))), 201


@bp.get("/api/v1/admin/places/<uuid:place_id>")
@admin_only
def admin_get(place_id: uuid.UUID):
    return no_store(jsonify(service.serialize_place(service.get_by_id(place_id), admin=True)))


@bp.patch("/api/v1/admin/places/<uuid:place_id>")
@admin_only
def admin_update(place_id: uuid.UUID):
    """Modificar la información del lugar: descripción, datos de la ruta, estado."""
    place = service.get_by_id(place_id)
    updated = service.update_place(place, load_body(PlaceFields(), partial=True))
    return no_store(jsonify(service.serialize_place(updated, admin=True)))


@bp.get("/api/v1/admin/places/<uuid:place_id>/history")
@admin_only
def admin_history(place_id: uuid.UUID):
    return no_store(jsonify({"items": service.history(service.get_by_id(place_id))}))


@bp.post("/api/v1/admin/places/<uuid:place_id>/photos")
@admin_only
def admin_add_photo(place_id: uuid.UUID):
    """Subir una foto (multipart: ``file``, ``alt_text`` y opcional ``is_cover``)."""
    place = service.get_by_id(place_id)
    upload = request.files.get("file")
    alt_text = clean_text(request.form.get("alt_text", ""))
    errors = []
    if upload is None:
        errors.append({"field": "file", "message": "Adjunta una foto."})
    if not 3 <= len(alt_text) <= 200:
        errors.append({"field": "alt_text", "message": "Describe la foto (3 a 200 caracteres) para accesibilidad."})
    if errors:
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.", errors=errors)
    limit = current_app.config["MAX_UPLOAD_BYTES"]
    data = upload.stream.read(limit + 1)             # nunca se lee más de lo permitido
    make_cover = request.form.get("is_cover", "").lower() in {"1", "true", "on", "yes"}
    photo = service.add_photo(place, data, alt_text, make_cover)
    return no_store(jsonify(service.serialize_photo(photo))), 201


@bp.patch("/api/v1/admin/photos/<uuid:photo_id>")
@admin_only
def admin_update_photo(photo_id: uuid.UUID):
    photo = service.update_photo(service.get_photo(photo_id), load_body(PhotoUpdateSchema(), partial=True))
    return no_store(jsonify(service.serialize_photo(photo)))


@bp.delete("/api/v1/admin/photos/<uuid:photo_id>")
@admin_only
def admin_delete_photo(photo_id: uuid.UUID):
    service.delete_photo(service.get_photo(photo_id))
    return "", 204
