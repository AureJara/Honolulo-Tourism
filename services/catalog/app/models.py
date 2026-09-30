from __future__ import annotations

import uuid
from datetime import datetime

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
