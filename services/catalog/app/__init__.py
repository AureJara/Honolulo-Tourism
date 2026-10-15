from __future__ import annotations

import os

import click
from flask import Flask

from honolulo_common.app_setup import (configure_logging, init_request_id, register_health,
                                       require_secrets)
from honolulo_common.errors import register_error_handlers

from .config import Config
from .extensions import db, migrate
from .storage import LocalStorage


def create_app(overrides: dict | None = None) -> Flask:
    configure_logging()
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    require_secrets(app, "JWT_SECRET_KEY")

    db.init_app(app)
    migrate.init_app(app, db)
    init_request_id(app)
    register_error_handlers(app)
    register_health(app, "catalog-service", db)
    app.extensions["storage"] = LocalStorage(app.config["MEDIA_ROOT"])

    from . import models  # noqa: F401
    from .api import bp
    from .reviews_api import bp as reviews_bp
    from .visits_api import bp as visits_bp
    from .tickets_api import bp as tickets_bp
    app.register_blueprint(bp)
    app.register_blueprint(reviews_bp)
    app.register_blueprint(visits_bp)
    app.register_blueprint(tickets_bp)

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        return resp

    @app.cli.command("seed")
    def seed_cmd():
        """Carga las cuatro cascadas del diseño y la foto inicial de Velo de las Ninfas (idempotente)."""
        from .seed import seed_photos, seed_places
        click.echo(f"Lugares creados: {seed_places()}")
        click.echo(f"Fotos iniciales cargadas: {seed_photos()}")
        if os.getenv("SEED_DEMO_VISITS", "").lower() in {"1", "true", "yes"}:
            from .visits import seed_demo_visits
            click.echo(f"Visitas de demostración cargadas: {seed_demo_visits()}")

    @app.cli.command("seed-visits")
    def seed_visits_cmd():
        """Carga (o completa) las visitas de DEMOSTRACIÓN de los últimos tres años. Idempotente."""
        from .visits import seed_demo_visits
        click.echo(f"Visitas de demostración cargadas: {seed_demo_visits()}")

    @app.cli.command("clear-demo-visits")
    def clear_demo_visits_cmd():
        """Elimina todas las visitas de demostración (las registradas por el personal no se tocan)."""
        from .visits import delete_demo
        click.echo(f"Visitas de demostración eliminadas: {delete_demo()}")

    return app
