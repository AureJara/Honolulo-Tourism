"""Lógica del catálogo: lugares, fotos y auditoría de los cambios del administrador."""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid

from flask import current_app, g
from sqlalchemy.orm import selectinload

from honolulo_common.errors import ApiError

from .extensions import db
from .images import THUMB_SIZE, ImageRejected, process_image
from .models import Place, PlaceAudit, PlacePhoto

log = logging.getLogger(__name__)

DIFFICULTY_LABELS = {"easy": "Suave / Fácil", "moderate": "Moderada", "hard": "Exigente"}
_EDITABLE = ("name", "description", "difficulty", "hike_minutes", "depth_label", "depth_min_m",
             "depth_max_m", "status", "sort_order")


def storage():
    return current_app.extensions["storage"]


# ------------------------------------------------------------ serialización
def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:70]


def unique_slug(name: str) -> str:
    base = slugify(name) or "lugar"
    candidate, n = base, 2
    while db.session.execute(db.select(Place.id).where(Place.slug == candidate)).first():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _depth_display(low, high) -> str | None:
    if high is None and low is None:
        return None
    if low is None:
        return f"Hasta {high:.1f} m"
    if high is None:
        return f"Desde {low:.1f} m"
    return f"{low:.1f} a {high:.1f} m"


def thumb_width(width: int, height: int) -> int:
    """Ancho de la miniatura (lado mayor ≤ 640 px): sirve para declarar bien el ``srcset``."""
    return max(1, round(width * min(1.0, THUMB_SIZE / max(width, height))))


def serialize_photo(photo: PlacePhoto) -> dict:
    return {"id": str(photo.id), "url": f"/media/{photo.key_full}", "thumb_url": f"/media/{photo.key_thumb}",
            "alt": photo.alt_text, "is_cover": photo.is_cover, "width": photo.width, "height": photo.height,
            "thumb_width": thumb_width(photo.width, photo.height)}


def serialize_place(place: Place, *, admin: bool = False) -> dict:
    photos = list(place.photos)
    cover = next((p for p in photos if p.is_cover), photos[0] if photos else None)
    body = {
        "id": str(place.id), "slug": place.slug, "name": place.name, "description": place.description,
        "difficulty": ({"code": place.difficulty, "label": DIFFICULTY_LABELS[place.difficulty]}
                       if place.difficulty else None),
        "hike": ({"minutes": place.hike_minutes, "label": f"{place.hike_minutes} min sendero"}
                 if place.hike_minutes else None),
        "depth": ({"label": place.depth_label or "Profundidad", "min_m": place.depth_min_m,
                   "max_m": place.depth_max_m, "display": _depth_display(place.depth_min_m, place.depth_max_m)}
                  if place.depth_min_m is not None or place.depth_max_m is not None else None),
        "cover": ({"url": f"/media/{cover.key_full}", "thumb_url": f"/media/{cover.key_thumb}", "alt": cover.alt_text,
                   "width": cover.width, "height": cover.height, "thumb_width": thumb_width(cover.width, cover.height)}
                  if cover else None),
        "photos": [serialize_photo(p) for p in photos],
        "updated_at": place.updated_at.isoformat() if place.updated_at else None,
    }
    if admin:
        body.update(status=place.status, sort_order=place.sort_order, updated_by=place.updated_by,
                    created_at=place.created_at.isoformat() if place.created_at else None)
    return body


# ---------------------------------------------------------------- consultas
def list_places(*, published_only: bool) -> list[Place]:
    query = db.select(Place).options(selectinload(Place.photos)).order_by(Place.sort_order, Place.name)
    if published_only:
        query = query.where(Place.status == "published")
    return list(db.session.execute(query).scalars())


def get_public(slug: str) -> Place:
    place = db.session.execute(
        db.select(Place).options(selectinload(Place.photos)).where(Place.slug == slug, Place.status == "published")
    ).scalar_one_or_none()
    if place is None:
        raise ApiError(404, "NOT_FOUND", "Lugar no encontrado.")
    return place


def get_by_id(place_id: uuid.UUID) -> Place:
    place = db.session.get(Place, place_id, options=[selectinload(Place.photos)])
    if place is None:
        raise ApiError(404, "NOT_FOUND", "Lugar no encontrado.")
    return place


def get_photo(photo_id: uuid.UUID) -> PlacePhoto:
    photo = db.session.get(PlacePhoto, photo_id)
    if photo is None:
        raise ApiError(404, "NOT_FOUND", "Foto no encontrada.")
    return photo


# ---------------------------------------------------------------- auditoría
def _audit(place: Place, action: str, changes: dict) -> None:
    db.session.add(PlaceAudit(place_id=place.id, actor_id=g.user["sub"], actor_name=(g.user.get("name") or "")[:100],
                              action=action, changes=changes))
    place.updated_by = g.user["sub"]


def history(place: Place, limit: int = 50) -> list[dict]:
    rows = db.session.execute(
        db.select(PlaceAudit).where(PlaceAudit.place_id == place.id)
        .order_by(PlaceAudit.id.desc()).limit(limit)).scalars()
    return [{"id": r.id, "action": r.action, "actor_id": r.actor_id, "actor_name": r.actor_name,
             "changes": r.changes, "created_at": r.created_at.isoformat()} for r in rows]


# ----------------------------------------------------------------- lugares
def create_place(data: dict) -> Place:
    place = Place(slug=unique_slug(data["name"]), status=data.pop("status", "draft"),
                  **{k: v for k, v in data.items() if k in _EDITABLE})
    db.session.add(place)
    db.session.flush()
    _audit(place, "created", {"name": [None, place.name], "status": [None, place.status]})
    db.session.commit()
    return get_by_id(place.id)


def update_place(place: Place, data: dict) -> Place:
    changes = {}
    for key in _EDITABLE:
        if key in data and data[key] != getattr(place, key):
            changes[key] = [getattr(place, key), data[key]]
            setattr(place, key, data[key])
    low, high = place.depth_min_m, place.depth_max_m
    if low is not None and high is not None and low > high:
        db.session.rollback()
        raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                       errors=[{"field": "depth_min_m", "message": "La profundidad mínima no puede superar la máxima."}])
    if changes:
        _audit(place, "updated", changes)
        db.session.commit()
    return get_by_id(place.id)


# ------------------------------------------------------------------- fotos
def add_photo(place: Place, data: bytes, alt_text: str, make_cover: bool) -> PlacePhoto:
    cfg = current_app.config
    if len(place.photos) >= cfg["MAX_PHOTOS_PER_PLACE"]:
        raise ApiError(409, "PHOTO_LIMIT", f"Un lugar admite hasta {cfg['MAX_PHOTOS_PER_PLACE']} fotos.")
    try:
        processed = process_image(data, cfg["MAX_UPLOAD_BYTES"])
    except ImageRejected as exc:
        if exc.too_large:
            raise ApiError(413, "FILE_TOO_LARGE", str(exc)) from exc
        raise ApiError(422, "INVALID_IMAGE", str(exc), errors=[{"field": "file", "message": str(exc)}]) from exc

    is_cover = make_cover or not place.photos
    if is_cover:
        db.session.execute(db.update(PlacePhoto).where(PlacePhoto.place_id == place.id, PlacePhoto.is_cover)
                           .values(is_cover=False))
    photo = PlacePhoto(id=uuid.uuid4(), place_id=place.id, alt_text=alt_text, is_cover=is_cover,
                       sort_order=max((p.sort_order for p in place.photos), default=-1) + 1,
                       width=processed.width, height=processed.height, bytes_full=len(processed.full),
                       uploaded_by=g.user["sub"])
    db.session.add(photo)
    db.session.flush()
    _audit(place, "photo_added", {"photo": str(photo.id), "alt_text": alt_text, "cover": is_cover})
    written: list[str] = []
    try:
        storage().write(photo.key_full, processed.full)
        written.append(photo.key_full)
        storage().write(photo.key_thumb, processed.thumb)
        written.append(photo.key_thumb)
        db.session.commit()
    except Exception:
        db.session.rollback()
        for key in written:                       # no dejar archivos huérfanos si falla algo
            storage().delete(key)
        raise
    return photo


def update_photo(photo: PlacePhoto, data: dict) -> PlacePhoto:
    place = get_by_id(photo.place_id)
    changes = {}
    if "alt_text" in data and data["alt_text"] != photo.alt_text:
        changes["alt_text"] = [photo.alt_text, data["alt_text"]]
        photo.alt_text = data["alt_text"]
    if "sort_order" in data and data["sort_order"] != photo.sort_order:
        changes["sort_order"] = [photo.sort_order, data["sort_order"]]
        photo.sort_order = data["sort_order"]
    if "is_cover" in data and data["is_cover"] != photo.is_cover:
        if not data["is_cover"]:
            raise ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.",
                           errors=[{"field": "is_cover", "message": "Elige otra foto como portada."}])
        db.session.execute(db.update(PlacePhoto).where(PlacePhoto.place_id == photo.place_id, PlacePhoto.is_cover)
                           .values(is_cover=False))
        photo.is_cover = True
        changes["cover"] = [False, True]
    if changes:
        _audit(place, "photo_updated", {"photo": str(photo.id), **changes})
        db.session.commit()
    return photo


def delete_photo(photo: PlacePhoto) -> None:
    place = get_by_id(photo.place_id)
    keys = [photo.key_full, photo.key_thumb]
    was_cover, photo_id = photo.is_cover, str(photo.id)
    db.session.delete(photo)
    db.session.flush()
    if was_cover:                                    # la portada siempre existe si hay fotos
        remaining = db.session.execute(
            db.select(PlacePhoto).where(PlacePhoto.place_id == place.id)
            .order_by(PlacePhoto.sort_order, PlacePhoto.created_at).limit(1)).scalar_one_or_none()
        if remaining is not None:
            remaining.is_cover = True
    _audit(place, "photo_deleted", {"photo": photo_id})
    db.session.commit()
    for key in keys:
        try:
            storage().delete(key)
        except OSError:
            log.warning("No se pudo borrar el archivo %s", key)
