from __future__ import annotations

import click
from flask import Flask
from werkzeug.security import generate_password_hash

from honolulo_common.app_setup import (configure_logging, init_request_id, register_health,
                                       require_production_secrets)
from honolulo_common.errors import register_error_handlers
from honolulo_common.timeutil import utcnow

from .config import Config
from .extensions import db, migrate


def create_app(overrides: dict | None = None) -> Flask:
    configure_logging()
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    require_production_secrets(app, "JWT_SECRET_KEY")
    if app.config["APP_ENV"] == "production" and app.config["MAIL_BACKEND"] != "smtp":
        raise RuntimeError("En producción MAIL_BACKEND debe ser 'smtp'")

    db.init_app(app)
    migrate.init_app(app, db)
    init_request_id(app)
    register_error_handlers(app)
    register_health(app, "auth-service", db)

    from . import models  # noqa: F401  (registra las tablas para Alembic)
    from .api import bp
    app.register_blueprint(bp)

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--first-name", prompt="Nombre")
    @click.option("--last-name", prompt="Apellido")
    @click.password_option()
    def create_admin(email, first_name, last_name, password):
        """Crea (o promueve) un administrador. Queda con el correo ya confirmado."""
        from .emails import canonical_email, normalize_email
        from .models import User
        email = normalize_email(email)
        user = db.session.execute(
            db.select(User).where(User.email_canonical == canonical_email(email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, email_canonical=canonical_email(email), first_name=first_name.strip(),
                        last_name=last_name.strip(), password_hash=generate_password_hash(password))
            db.session.add(user)
        user.role = "admin"
        user.email_verified_at = user.email_verified_at or utcnow()
        db.session.commit()
        click.echo(f"Administrador listo: {email}")

    @app.cli.command("set-role")
    @click.argument("email")
    @click.argument("role", type=click.Choice(["user", "admin"]))
    def set_role(email, role):
        """Cambia el rol de un usuario existente."""
        from .emails import canonical_email
        from .models import User
        user = db.session.execute(
            db.select(User).where(User.email_canonical == canonical_email(email))).scalar_one_or_none()
        if user is None:
            raise click.ClickException("Usuario no encontrado")
        user.role = role
        db.session.commit()
        click.echo(f"{user.email} → {role}")

    return app
