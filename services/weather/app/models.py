from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from .extensions import db


def _num(precision: int, scale: int):
    return sa.Numeric(precision, scale, asdecimal=False)


class WeatherObservation(db.Model):
    """Historial meteorológico (RF08): una fila por lectura obtenida del proveedor externo.

    ``source='current'``: lectura en tiempo real (la precipitación puede ser de 15 min).
    ``source='hourly'``: serie horaria del proveedor para horas ya cumplidas (precipitación de
    1 h, comparable con el pronóstico); es la que usa la evaluación del pronóstico (RN06).
    """

    __tablename__ = "weather_observations"
    __table_args__ = (
        sa.UniqueConstraint("location_code", "observed_at", "source", name="uq_obs_location_time_source"),
        sa.CheckConstraint("humidity_pct BETWEEN 0 AND 100", name="ck_obs_humidity"),
        sa.CheckConstraint("rain_probability_pct BETWEEN 0 AND 100", name="ck_obs_rain_prob"),
        sa.CheckConstraint("cloud_cover_pct BETWEEN 0 AND 100", name="ck_obs_cloud"),
        sa.CheckConstraint("precipitation_mm >= 0", name="ck_obs_precip"),
        sa.CheckConstraint("wind_kph >= 0", name="ck_obs_wind"),
        sa.CheckConstraint("source IN ('current','hourly')", name="ck_obs_source"),
        sa.Index("ix_obs_location_time", "location_code", sa.text("observed_at DESC")),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    location_code: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    temperature_c: Mapped[float] = mapped_column(_num(4, 1), nullable=False)
    feels_like_c: Mapped[float | None] = mapped_column(_num(4, 1))
    humidity_pct: Mapped[int | None] = mapped_column(sa.SmallInteger)
    precipitation_mm: Mapped[float] = mapped_column(_num(5, 2), nullable=False, default=0, server_default="0")
    rain_probability_pct: Mapped[int | None] = mapped_column(sa.SmallInteger)
    wind_kph: Mapped[float | None] = mapped_column(_num(5, 1))
    cloud_cover_pct: Mapped[int | None] = mapped_column(sa.SmallInteger)
    weather_code: Mapped[int | None] = mapped_column(sa.SmallInteger)
    condition: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    is_day: Mapped[bool | None] = mapped_column(sa.Boolean)
    source: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class ProviderFetchLog(db.Model):
    """Bitácora de cada llamada al proveedor (latencia, estado, error)."""

    __tablename__ = "provider_fetch_log"
    __table_args__ = (
        sa.CheckConstraint("kind IN ('current','hourly')", name="ck_fetch_kind"),
        sa.Index("ix_fetch_kind_time", "kind", sa.text("fetched_at DESC")),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    ok: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    http_status: Mapped[int | None] = mapped_column(sa.SmallInteger)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    error: Mapped[str | None] = mapped_column(sa.String(300))
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
