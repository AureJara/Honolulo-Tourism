from __future__ import annotations

import click
from flask import Flask

from honolulo_common.app_setup import (configure_logging, init_request_id, register_health,
                                       require_production_secrets)
from honolulo_common.errors import register_error_handlers
from honolulo_common.location import Location
from honolulo_common.openmeteo import OpenMeteoClient

from .config import Config
from .extensions import db, migrate


def create_app(overrides: dict | None = None) -> Flask:
    configure_logging()
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    require_production_secrets(app, "JWT_SECRET_KEY", "INTERNAL_API_TOKEN")

    db.init_app(app)
    migrate.init_app(app, db)
    init_request_id(app)
    register_error_handlers(app)
    register_health(app, "forecast-service", db)

    app.extensions["location"] = Location.from_env()
    app.extensions["weather_client"] = OpenMeteoClient(
        base_url=app.config["PROVIDER_BASE_URL"], timeout=app.config["PROVIDER_TIMEOUT_S"])

    from . import models  # noqa: F401
    from .api import bp
    app.register_blueprint(bp)

    @app.cli.command("refresh-forecast")
    def refresh_forecast_cmd():
        """Consulta el pronóstico ahora mismo y lo guarda como una emisión nueva."""
        from . import service
        click.echo(f"Filas guardadas: {service.refresh_forecast()}")

    @app.cli.command("evaluate")
    def evaluate_cmd():
        """Evalúa los pronósticos vencidos contra lo observado."""
        from . import service
        click.echo(f"Pronósticos evaluados: {service.evaluate_pending()}")

    @app.cli.command("seed")
    def seed_cmd():
        """Crea la versión inicial de los parámetros de puntuación (idempotente)."""
        from . import service
        click.echo(f"Parámetros de puntuación activos: versión {service.active_params().version}")

    return app
