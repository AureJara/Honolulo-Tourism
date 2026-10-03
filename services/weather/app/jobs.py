"""Tareas programadas (APScheduler, en proceso).

Ejecutar con **un solo proceso worker** (``gunicorn --workers 1 --threads 4``) para no duplicar
consultas al proveedor; ver README. Todas las tareas son idempotentes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from honolulo_common.weather_provider import ProviderError
from honolulo_common.timeutil import utcnow

log = logging.getLogger(__name__)


def _in_context(app: Flask, func):
    def runner():
        with app.app_context():
            try:
                func()
            except ProviderError:
                pass  # ya registrado en la bitácora; se reintenta en la próxima corrida
            except Exception:  # noqa: BLE001
                log.exception("Falla en la tarea programada %s", func.__name__)
    runner.__name__ = func.__name__
    return runner


def refresh_current_job() -> None:
    from . import service
    service.refresh_current()


def sync_hourly_job() -> None:
    from . import service
    service.sync_hourly()


def start_scheduler(app: Flask) -> BackgroundScheduler | None:
    if not app.config["ENABLE_SCHEDULER"]:
        return None
    tz = app.extensions["location"].timezone
    scheduler = BackgroundScheduler(timezone=tz)
    now = utcnow()
    scheduler.add_job(
        _in_context(app, refresh_current_job), "interval",
        minutes=app.config["REFRESH_CURRENT_MINUTES"], next_run_time=now + timedelta(seconds=3),
        id="refresh_current", max_instances=1, coalesce=True)
    # Al arrancar y luego cada hora, para tener la serie horaria disponible para evaluar.
    scheduler.add_job(
        _in_context(app, sync_hourly_job), "interval", hours=1,
        next_run_time=now + timedelta(seconds=15), id="sync_hourly", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("Scheduler de weather-service iniciado")
    return scheduler
