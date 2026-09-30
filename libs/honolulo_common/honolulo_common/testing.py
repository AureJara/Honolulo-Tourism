"""Ayudas para pruebas: PostgreSQL real (nunca SQLite) con una base por ejecución.

Orden de preferencia:
1. ``TEST_DATABASE_URL`` (servidor PostgreSQL existente, p. ej. el de docker compose).
2. PostgreSQL embebido con ``pgserver`` (``pip install pgserver``), útil sin Docker.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

from .openmeteo import ProviderError, Reading

_embedded = None


class FakeProvider:
    """Sustituye al proveedor externo: las pruebas nunca salen a internet."""

    def __init__(self):
        self.current = None
        self.hourly = []
        self.fail = False
        self.calls = []
        self.last_stats = None

    def fetch_current(self, lat, lon, tz):
        self.calls.append("current")
        if self.fail:
            raise ProviderError("proveedor caído")
        return self.current

    def fetch_hourly(self, lat, lon, tz, *, forecast_days=16, past_days=0):
        self.calls.append(("hourly", past_days))
        if self.fail:
            raise ProviderError("proveedor caído")
        return self.hourly


def make_reading(when=None, temp=26.4, code=2, is_day=True, **kw) -> Reading:
    when = when or datetime.now(timezone.utc).replace(second=0, microsecond=0)
    values = dict(time_utc=when, temperature_c=temp, feels_like_c=27.1, humidity_pct=78,
                  precipitation_mm=0.4, rain_probability_pct=35, wind_kph=6.2,
                  cloud_cover_pct=64, weather_code=code, is_day=is_day)
    values.update(kw)
    return Reading(**values)


def _server_url() -> str:
    global _embedded
    env = os.getenv("TEST_DATABASE_URL")
    if env:
        return env
    import pgserver  # import diferido: solo se necesita en desarrollo

    if _embedded is None:
        data_dir = Path(tempfile.gettempdir()) / "honolulo_pg_tests"
        try:
            _embedded = pgserver.get_server(data_dir, cleanup_mode="stop")
        except Exception:                      # noqa: BLE001
            # El directorio temporal puede quedar dañado (p. ej. limpieza de %TEMP% en Windows): es solo
            # un servidor desechable de pruebas, así que se recrea desde cero.
            shutil.rmtree(data_dir, ignore_errors=True)
            _embedded = pgserver.get_server(data_dir, cleanup_mode="stop")
    return _embedded.get_uri()


def create_test_database(prefix: str) -> str:
    """Crea una base vacía y devuelve su URL SQLAlchemy (driver psycopg 3)."""
    base = _server_url().replace("postgresql://", "postgresql+psycopg://", 1)
    plain = base.replace("postgresql+psycopg://", "postgresql://", 1)
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(plain, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    head, _, _ = base.rpartition("/")
    return f"{head}/{name}"
