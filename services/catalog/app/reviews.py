"""Opiniones y puntuación de los visitantes sobre cada lugar, y su moderación por el administrador.

Una opinión por persona y lugar; el nombre público es solo «Nombre I.». Las acciones de moderación (fijar, eliminar)
quedan en ``place_audit`` con quién las hizo, sin copiar el texto eliminado. Quien actúa llega como parámetro
(``claims``, las claims del JWT): este módulo no depende de la petición HTTP ni de la administración de lugares: las rutas están en ``reviews_api`` y la limpieza del texto en ``review_schemas``.
"""

from __future__ import annotations

import uuid

from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from honolulo_common.errors import ApiError
from honolulo_common.timeutil import utcnow

from .extensions import db
from .models import Place, PlaceAudit, PlaceReview


def author_display(claims: dict) -> str:
    """Nombre público de la opinión: «Nombre I.» (nunca el apellido completo ni el correo)."""
    full = (claims.get("name") or "").strip()
    first = (claims.get("first_name") or "").strip() or (full.split(" ", 1)[0] if full else "")
    rest = full[len(first):].strip() if first and full.startswith(first) else ""
    initial = next((c for c in rest if c.isalpha()), "")
    return (f"{first} {initial.upper()}." if initial else first or "Visitante")[:60]


def rating_summaries(place_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Promedio (1 decimal) y cantidad de puntuaciones por lugar, en una sola consulta."""
    if not place_ids:
        return {}
    rows = db.session.execute(
        db.select(PlaceReview.place_id, func.count(), func.avg(PlaceReview.rating))
        .where(PlaceReview.place_id.in_(place_ids)).group_by(PlaceReview.place_id)).all()
    return {pid: {"average": round(float(avg) + 1e-9, 1), "count": n} for pid, n, avg in rows}


def review_summary(place: Place) -> dict:
    rows = db.session.execute(
        db.select(PlaceReview.rating, func.count()).where(PlaceReview.place_id == place.id)
        .group_by(PlaceReview.rating)).all()
    distribution = {str(stars): 0 for stars in range(5, 0, -1)}
    for stars, n in rows:
        distribution[str(stars)] = n
    count = sum(distribution.values())
    total = sum(int(stars) * n for stars, n in distribution.items())
    return {"average": round(total / count + 1e-9, 1) if count else None, "count": count,
            "distribution": distribution}


def serialize_review(review: PlaceReview) -> dict:
    return {"id": str(review.id), "author": review.author_name, "rating": review.rating,
            "comment": review.comment, "pinned": review.pinned,
            "created_at": review.created_at.isoformat() if review.created_at else None}


def reviews_page(place: Place, limit: int, offset: int) -> dict:
    """Opiniones del lugar: primero las fijadas por el administrador y después las más recientes."""
    query = (db.select(PlaceReview).where(PlaceReview.place_id == place.id)
             .order_by(PlaceReview.pinned.desc(), PlaceReview.pinned_at.desc().nulls_last(),
                       PlaceReview.created_at.desc(), PlaceReview.id)
             .limit(limit).offset(offset))
    items = [serialize_review(r) for r in db.session.execute(query).scalars()]
    summary = review_summary(place)
    return {"summary": summary, "items": items, "total": summary["count"], "limit": limit, "offset": offset}


def get_my_review(place: Place, user_id: str) -> PlaceReview | None:
    return db.session.execute(db.select(PlaceReview).where(
        PlaceReview.place_id == place.id, PlaceReview.user_id == user_id)).scalar_one_or_none()


def _audit_review(place: Place, claims: dict, action: str, changes: dict) -> None:
    """Auditoría de moderación. No modifica ``place.updated_by``: el lugar en sí no se editó."""
    db.session.add(PlaceAudit(place_id=place.id, actor_id=claims["sub"], actor_name=(claims.get("name") or "")[:100],
                              action=action, changes=changes))


def upsert_review(place: Place, claims: dict, data: dict) -> tuple[PlaceReview, bool]:
    """Crea o actualiza la opinión de la persona autenticada. Devuelve (opinión, ¿fue creada?)."""
    for attempt in (1, 2):
        review = get_my_review(place, claims["sub"])
        created = review is None
        if created:
            review = PlaceReview(place_id=place.id, user_id=claims["sub"], rating=data["rating"],
                                 comment=data["comment"], author_name=author_display(claims))
            db.session.add(review)
        else:
            text_changed = review.comment != data["comment"]
            review.rating, review.comment, review.author_name = data["rating"], data["comment"], author_display(claims)
            if review.pinned and text_changed:        # un texto editado ya no es el que el administrador fijó
                review.pinned, review.pinned_at, review.pinned_by = False, None, None
                _audit_review(place, claims, "review_unpinned", {"review": str(review.id), "reason": "edited_by_author"})
        try:
            db.session.commit()
            return review, created
        except IntegrityError:                        # dos primeras opiniones simultáneas de la misma persona
            db.session.rollback()
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def delete_my_review(place: Place, claims: dict) -> None:
    review = get_my_review(place, claims["sub"])
    if review is None:
        raise ApiError(404, "NOT_FOUND", "Aún no has opinado sobre este lugar.")
    db.session.delete(review)
    db.session.commit()


def get_review(review_id: uuid.UUID) -> PlaceReview:
    review = db.session.get(PlaceReview, review_id)
    if review is None:
        raise ApiError(404, "NOT_FOUND", "Opinión no encontrada.")
    return review


def moderator_delete_review(review: PlaceReview, claims: dict) -> None:
    """El administrador elimina una opinión. En la auditoría queda quién, cuándo y de quién, pero no el texto."""
    place = db.session.get(Place, review.place_id)
    _audit_review(place, claims, "review_deleted", {"review": str(review.id), "author_id": review.user_id,
                                             "rating": review.rating, "pinned": review.pinned})
    db.session.delete(review)
    db.session.commit()


def moderator_pin_review(review: PlaceReview, pinned: bool, claims: dict) -> PlaceReview:
    """Fija o quita de fijadas una opinión (hasta ``REVIEW_MAX_PINNED`` por lugar). Es idempotente."""
    if review.pinned == pinned:
        return review
    place = db.session.get(Place, review.place_id)
    # Se bloquea el lugar para que dos administradores no superen el límite a la vez.
    db.session.execute(db.select(Place.id).where(Place.id == place.id).with_for_update())
    db.session.refresh(review)
    if review.pinned == pinned:
        db.session.rollback()
        return review
    if pinned:
        limit = current_app.config["REVIEW_MAX_PINNED"]
        used = db.session.execute(db.select(func.count()).select_from(PlaceReview).where(
            PlaceReview.place_id == place.id, PlaceReview.pinned)).scalar_one()
        if used >= limit:
            db.session.rollback()
            raise ApiError(409, "PIN_LIMIT", f"Solo se pueden fijar hasta {limit} opiniones por lugar. "
                                              "Quita una fijada para fijar otra.")
        review.pinned, review.pinned_at, review.pinned_by = True, utcnow(), claims["sub"]
    else:
        review.pinned, review.pinned_at, review.pinned_by = False, None, None
    _audit_review(place, claims, "review_pinned" if pinned else "review_unpinned", {"review": str(review.id)})
    db.session.commit()
    return review
