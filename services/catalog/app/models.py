from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db

JSON_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")


def _num(precision: int, scale: int):
    return sa.Numeric(precision, scale, asdecimal=False)


class Place(db.Model):
    """Lugar turístico (cascadas del diseño). El administrador edita su descripción y sus fotos."""

    __tablename__ = "places"
    __table_args__ = (
        sa.CheckConstraint("difficulty IN ('easy','moderate','hard')", name="ck_places_difficulty"),
        sa.CheckConstraint("status IN ('draft','published','archived')", name="ck_places_status"),
        sa.CheckConstraint("hike_minutes > 0", name="ck_places_hike"),
        sa.CheckConstraint("depth_min_m IS NULL OR depth_max_m IS NULL OR depth_min_m <= depth_max_m",
                           name="ck_places_depth_order"),
        sa.Index("ix_places_public", "sort_order", "name", postgresql_where=sa.text("status = 'published'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(sa.String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    difficulty: Mapped[str | None] = mapped_column(sa.String(10))
    hike_minutes: Mapped[int | None] = mapped_column(sa.SmallInteger)
    depth_label: Mapped[str | None] = mapped_column(sa.String(40))
    depth_min_m: Mapped[float | None] = mapped_column(_num(3, 1))
    depth_max_m: Mapped[float | None] = mapped_column(_num(3, 1))
    status: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="draft", server_default="draft")
    sort_order: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False, default=0, server_default="0")
    updated_by: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now())

    photos: Mapped[list["PlacePhoto"]] = relationship(
        back_populates="place", cascade="all, delete-orphan", order_by="PlacePhoto.sort_order, PlacePhoto.created_at")


class PlacePhoto(db.Model):
    __tablename__ = "place_photos"
    __table_args__ = (
        sa.Index("ux_place_single_cover", "place_id", unique=True, postgresql_where=sa.text("is_cover")),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    place_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("places.id", ondelete="CASCADE"), nullable=False, index=True)
    alt_text: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    is_cover: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.false())
    sort_order: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False, default=0, server_default="0")
    width: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    height: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    bytes_full: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())

    place: Mapped[Place] = relationship(back_populates="photos")

    @property
    def key_full(self) -> str:
        return f"{self.id.hex}-1600.webp"

    @property
    def key_thumb(self) -> str:
        return f"{self.id.hex}-640.webp"


class PlaceAudit(db.Model):
    """Quién cambió qué y cuándo (trazabilidad de las ediciones del administrador)."""

    __tablename__ = "place_audit"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    place_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("places.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    actor_name: Mapped[str | None] = mapped_column(sa.String(100))
    action: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    changes: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


class PlaceReview(db.Model):
    """Opinión de una persona sobre un lugar: puntuación de 1 a 5 y un comentario opcional.

    Hay una sola por persona y lugar (se edita, no se acumula). El nombre público es solo «Nombre I.» y no se
    guarda ningún dato de contacto: ``user_id`` es el identificador del servicio de cuentas y no se expone.
    El administrador puede fijar (``pinned``) o eliminar opiniones; cada acción queda en ``place_audit``.
    """

    __tablename__ = "place_reviews"
    __table_args__ = (
        sa.UniqueConstraint("place_id", "user_id", name="uq_review_place_user"),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating"),
        sa.CheckConstraint("comment IS NULL OR char_length(comment) BETWEEN 3 AND 1000", name="ck_reviews_comment_len"),
        sa.CheckConstraint("(pinned AND pinned_at IS NOT NULL) OR (NOT pinned AND pinned_at IS NULL)",
                           name="ck_reviews_pin_consistent"),
        sa.Index("ix_place_reviews_listing", "place_id", "pinned", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    place_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("places.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    author_name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    rating: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(sa.Text)
    pinned: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.false())
    pinned_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    pinned_by: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now())


class PlaceVisit(db.Model):
    """Visitantes de un lugar en un día. ``source``: ``registro`` (anotado por el personal) o ``demo`` (datos de demostración)."""

    __tablename__ = "place_visits"
    __table_args__ = (
        sa.UniqueConstraint("place_id", "visit_date", name="uq_visit_place_day"),
        sa.CheckConstraint("visitors BETWEEN 0 AND 50000", name="ck_visits_range"),
        sa.CheckConstraint("source IN ('registro','demo')", name="ck_visits_source"),
        sa.Index("ix_place_visits_date", "visit_date"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True,
                                    autoincrement=True)
    place_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("places.id", ondelete="CASCADE"), nullable=False)
    visit_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    visitors: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    source: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="registro", server_default="registro")
    recorded_by: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now())


class TicketOrder(db.Model):
    """Compra de boletos de entrada a un lugar. ``status``: ``pagado`` → ``usado`` (validado en la puerta) o ``anulado``.

    El pago es SIMULADO: no se guardan datos de tarjeta ni se contacta a ninguna pasarela real.
    """

    __tablename__ = "ticket_orders"
    __table_args__ = (
        sa.CheckConstraint("quantity BETWEEN 1 AND 20", name="ck_tickets_quantity"),
        sa.CheckConstraint("status IN ('pagado','usado','anulado')", name="ck_tickets_status"),
        sa.CheckConstraint("payment_method IN ('yape','plin','tarjeta','efectivo')", name="ck_tickets_method"),
        sa.CheckConstraint("unit_price_cents >= 0 AND total_cents = unit_price_cents * quantity", name="ck_tickets_total"),
        sa.Index("ix_ticket_orders_user", "user_id", "created_at"),
        sa.Index("ix_ticket_orders_date", "visit_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.String(16), nullable=False, unique=True)
    place_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("places.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    buyer_name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    visit_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    total_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    payment_method: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(10), nullable=False, default="pagado", server_default="pagado")
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
