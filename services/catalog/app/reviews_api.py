"""Rutas HTTP de las opiniones: lectura pública, mi opinión y moderación del administrador.

La lógica está en ``reviews`` y la validación del texto en ``review_schemas``; aquí solo se traduce HTTP.
"""

from __future__ import annotations

import uuid

from flask import Blueprint, current_app, g, jsonify, request

from honolulo_common.errors import ApiError

from . import reviews, service
from .permissions import admin_only, signed_in
from .responses import no_store
from .review_schemas import PinSchema, load_review
from .schemas import load_body

bp = Blueprint("reviews", __name__)


def _page_args() -> tuple[int, int]:
    """``limit`` (1–50) y ``offset`` (≥ 0) de la lista de opiniones."""
    errors = []
    default = current_app.config["REVIEWS_PAGE_SIZE"]
    values = {}
    for key, low, high, fallback in (("limit", 1, 50, default), ("offset", 0, 100_000, 0)):
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


@bp.get("/api/v1/places/<slug>/reviews")
def list_reviews(slug: str):
    """Opiniones públicas del lugar: las fijadas primero. Nunca se cachean (una eliminación debe verse al instante)."""
    place = service.get_public(slug)
    return no_store(jsonify(reviews.reviews_page(place, *_page_args())))


@bp.get("/api/v1/places/<slug>/reviews/mine")
@signed_in
def my_review(slug: str):
    review = reviews.get_my_review(service.get_public(slug), g.user["sub"])
    return no_store(jsonify({"review": reviews.serialize_review(review) if review else None}))


@bp.put("/api/v1/places/<slug>/reviews/mine")
@signed_in
def put_my_review(slug: str):
    """Publica o actualiza mi opinión (una por persona y lugar)."""
    place = service.get_public(slug)
    review, created = reviews.upsert_review(place, g.user, load_review(current_app.config["REVIEW_COMMENT_MAX"]))
    body = {"review": reviews.serialize_review(review), "created": created,
            "summary": reviews.review_summary(place)}
    return no_store(jsonify(body)), (201 if created else 200)


@bp.delete("/api/v1/places/<slug>/reviews/mine")
@signed_in
def delete_my_review(slug: str):
    place = service.get_public(slug)
    reviews.delete_my_review(place, g.user)
    return no_store(jsonify({"summary": reviews.review_summary(place)})), 200


# ------------------------------------------- moderación (solo administrador)
@bp.delete("/api/v1/admin/reviews/<uuid:review_id>")
@admin_only
def admin_delete_review(review_id: uuid.UUID):
    reviews.moderator_delete_review(reviews.get_review(review_id), g.user)
    return "", 204


@bp.put("/api/v1/admin/reviews/<uuid:review_id>/pin")
@admin_only
def admin_pin_review(review_id: uuid.UUID):
    """Fija (``{"pinned": true}``) o quita de fijadas una opinión."""
    review = reviews.moderator_pin_review(reviews.get_review(review_id), load_body(PinSchema())["pinned"], g.user)
    return no_store(jsonify(reviews.serialize_review(review)))
