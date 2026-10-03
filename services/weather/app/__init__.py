from __future__ import annotations

import click
from flask import Flask

from honolulo_common.app_setup import (configure_logging, init_request_id, register_health,
                                       require_secrets)
from honolulo_common.errors import register_error_handlers
from honolulo_common.location import Location
from honolulo_common.weather_provider import create_provider

from .config import Config
from .extensions import db, migrate


def create_app(overrides: dict | None = None) -> Flask:
    configure_logging()
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    require_secrets(app, "JWT_SECRET_KEY", "INTERNAL_API_TOKEN")

    db.init_app(app)
    migrate.init_app(app, db)
    init_request_id(app)
    register_error_handlers(app)
    register_health(app, "weather-service", db)

    app.extensions["location"] = Location.from_env()
    app.extensions["weather_client"] = create_provider(app.config)

    from . import models  # noqa: F401
    from .api import bp
    app.register_blueprint(bp)

    @app.cli.command("refresh-current")
    def refresh_current_cmd():
        """Consulta el clima actual ahora mismo y lo guarda."""
        from . import service
        obs = service.refresh_current()
        click.echo(f"Lectura guardada: {obs.observed_at.isoformat()} {obs.temperature_c}°C")

    @app.cli.command("sync-hourly")
    @click.option("--days", default=3, show_default=True)
    def sync_hourly_cmd(days):
        """Sincroniza la serie horaria de los últimos días."""
        from . import service
        click.echo(f"Filas escritas: {service.sync_hourly(days)}")

    return app
