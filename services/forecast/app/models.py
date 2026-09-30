from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .extensions import db

JSON_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")


def _num(precision: int, scale: int):
    return sa.Numeric(precision, scale, asdecimal=False)


class WeatherForecast(db.Model):
    """Pronóstico horario (RF09 / RN05). Tabla de solo inserción: cada emisión del proveedor agrega
    filas nuevas; nunca se sobrescriben, para poder evaluarlas después contra lo observado."""

    __tablename__ = "weather_forecasts"
    __table_args__ = (
        sa.UniqueConstraint("location_code", "issued_at", "target_at", name="uq_forecast_issue_target"),
        sa.CheckConstraint("humidity_pct BETWEEN 0 AND 100", name="ck_fc_humidity"),
        sa.CheckConstraint("rain_probability_pct BETWEEN 0 AND 100", name="ck_fc_rain_prob"),
        sa.CheckConstraint("cloud_cover_pct BETWEEN 0 AND 100", name="ck_fc_cloud"),
        sa.CheckConstraint("precipitation_mm >= 0", name="ck_fc_precip"),
        sa.CheckConstraint("wind_kph >= 0", name="ck_fc_wind"),
        sa.Index("ix_fc_target", "location_code", "target_at", sa.text("issued_at DESC")),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    location_code: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)   # fecha de emisión
    target_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)   # fecha y hora objetivo
    temperature_c: Mapped[float] = mapped_column(_num(4, 1), nullable=False)
    feels_like_c: Mapped[float | None] = mapped_column(_num(4, 1))
    humidity_pct: Mapped[int | None] = mapped_column(sa.SmallInteger)
    precipitation_mm: Mapped[float] = mapped_column(_num(5, 2), nullable=False, default=0, server_default="0")
    rain_probability_pct: Mapped[int | None] = mapped_column(sa.SmallInteger)
    wind_kph: Mapped[float | None] = mapped_column(_num(5, 1))
    cloud_cover_pct: Mapped[int | None] = mapped_column(sa.SmallInteger)
    weather_code: Mapped[int | None] = mapped_column(sa.SmallInteger)
    condition: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(30), nullable=False)


class ScoringParameters(db.Model):
    """Fórmula de puntuación como parámetro del sistema (RF11). Versionada: solo una activa."""

    __tablename__ = "scoring_parameters"
    __table_args__ = (
        sa.Index("ux_scoring_single_active", "is_active", unique=True, postgresql_where=sa.text("is_active")),
    )

    version: Mapped[int] = mapped_column(sa.SmallInteger, primary_key=True, autoincrement=True)
    weights: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    tolerances: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    rain_prob_threshold_pct: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False, default=50)
    rain_observed_threshold_mm: Mapped[float] = mapped_column(_num(3, 1), nullable=False, default=0.1)
    labels: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.false())
    created_by: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


class ForecastEvaluation(db.Model):
    """Resultado de comparar un pronóstico con la observación real (RF10 / RF11)."""

    __tablename__ = "forecast_evaluations"
    __table_args__ = (
        sa.UniqueConstraint("forecast_id", "params_version", name="uq_eval_forecast_params"),
        sa.CheckConstraint("accuracy_score BETWEEN 0 AND 100", name="ck_eval_score"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    forecast_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("weather_forecasts.id", ondelete="CASCADE"), nullable=False, index=True)
    observation_id: Mapped[int | None] = mapped_column(sa.BigInteger)   # id en weather-service (sin FK entre servicios)
    params_version: Mapped[int] = mapped_column(
        sa.SmallInteger, sa.ForeignKey("scoring_parameters.version"), nullable=False)
    accuracy_score: Mapped[float] = mapped_column(_num(5, 2), nullable=False)
    accuracy_label: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    variables: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class ProviderFetchLog(db.Model):
    __tablename__ = "provider_fetch_log"
    __table_args__ = (sa.Index("ix_fetch_time", sa.text("fetched_at DESC")),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    ok: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    http_status: Mapped[int | None] = mapped_column(sa.SmallInteger)
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    error: Mapped[str | None] = mapped_column(sa.String(300))
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
