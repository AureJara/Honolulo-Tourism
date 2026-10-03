"""Tareas programadas (APScheduler, en proceso). Ejecutar con un solo proceso worker."""

from __future__ import annotations

import logging
from datetime import timedelta

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


def refresh_forecast_job() -> None:
    from . import service
    service.refresh_forecast()


def evaluate_job() -> None:
    from . import service
    service.evaluate_pending()


def purge_job() -> None:
    from . import service
    service.purge_old()


def start_scheduler(app: Flask) -> BackgroundScheduler | None:
    if not app.config["ENABLE_SCHEDULER"]:
        return None
    from . import service
    tz = app.extensions["location"].timezone
    scheduler = BackgroundScheduler(timezone=tz)
    now = utcnow()

    # Si ya hay una emisión reciente no se repite al arrancar (evita gastar cuota al reiniciar).
    hours = app.config["FORECAST_REFRESH_HOURS"]
    first_run = now + timedelta(seconds=5)
    with app.app_context():
        try:
            last = service.last_issue_at()
            if last and now - last < timedelta(hours=hours):
                first_run = last + timedelta(hours=hours)
        except Exception:  # noqa: BLE001  (tablas aún no creadas, etc.)
            log.warning("No se pudo leer la última emisión; se programa refresco inmediato")
    scheduler.add_job(_in_context(app, refresh_forecast_job), "interval", hours=hours, next_run_time=first_run,
                      id="refresh_forecast", max_instances=1, coalesce=True)
    scheduler.add_job(_in_context(app, evaluate_job), "interval", hours=1,
                      next_run_time=now + timedelta(seconds=45), id="evaluate_forecasts",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_in_context(app, purge_job), "interval", days=7,
                      next_run_time=now + timedelta(minutes=10), id="purge", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("Scheduler de forecast-service iniciado")
    return scheduler
