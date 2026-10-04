from __future__ import annotations

import click
from flask import Flask
from werkzeug.security import generate_password_hash

from honolulo_common.app_setup import (configure_logging, init_request_id, register_health,
                                       require_secrets)
from honolulo_common.errors import register_error_handlers
from honolulo_common.timeutil import utcnow

from . import audit, mailer
from .config import Config
from .extensions import db, migrate


def create_app(overrides: dict | None = None) -> Flask:
    configure_logging()
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    require_secrets(app, "JWT_SECRET_KEY")
    if app.config["APP_ENV"] == "production" and not mailer.backend(app.config["MAIL_BACKEND"]).external:
        raise RuntimeError("En producción MAIL_BACKEND debe enviar por Internet (smtp)")

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
    def create_admin(email):
        """Crea un administrador (o promueve una cuenta existente). La contraseña se pide sin mostrarla."""
        from .emails import canonical_email, normalize_email
        from .models import User
        from .names import InvalidName, clean_person_name
        from .passwords import check_password
        email = normalize_email(email)
        user = db.session.execute(
            db.select(User).where(User.email_canonical == canonical_email(email))).scalar_one_or_none()
        if user is None:
            try:
                first_name = clean_person_name(click.prompt("Nombre"))
                last_name = clean_person_name(click.prompt("Apellido"))
            except InvalidName as exc:
                raise click.ClickException(str(exc)) from exc
            password = click.prompt("Contraseña", hide_input=True, confirmation_prompt=True)
            problem = check_password(password, min_len=app.config["PASSWORD_MIN_LENGTH"],
                                     personal=(first_name, last_name, email.split("@")[0]))
            if problem:
                raise click.ClickException(problem)
            user = User(email=email, email_canonical=canonical_email(email), first_name=first_name,
                        last_name=last_name,
                        password_hash=generate_password_hash(password, method=app.config["PASSWORD_HASH_METHOD"]))
            db.session.add(user)
            db.session.flush()
            audit.record(audit.Event.ADMIN_CREATED, user=user, source="cli")
        elif user.role != "admin":
            audit.record(audit.Event.ROLE_CHANGED, user=user, old=user.role, new="admin", source="cli")
        user.role = "admin"
        user.email_verified_at = user.email_verified_at or utcnow()
        db.session.commit()
        click.echo(f"Administrador listo: {email}")

    @app.cli.command("mail-test")
    @click.argument("to")
    def mail_test(to):
        """Envía un correo de prueba con la configuración de correo actual (no contiene ningún código)."""
        from email.message import EmailMessage

        name = app.config["MAIL_BACKEND"]
        message = EmailMessage()
        message["Subject"] = "Prueba de correo de Honolulo"
        message["From"] = app.config["MAIL_FROM"]
        message["To"] = to
        message.set_content("Este es un mensaje de prueba. Si lo recibes, Honolulo puede enviar los códigos "
                            "de confirmación a los correos de los usuarios.")
        try:
            mailer.send(message)
        except mailer.MailError as exc:
            raise click.ClickException(f"No se pudo enviar ({name}): {exc}") from exc
        chosen = mailer.backend(name)
        if chosen.external:
            click.echo(f"Correo de prueba enviado a {to} (backend '{name}'). Revisa tu bandeja (y spam).")
        else:
            where = f" (quedó en {chosen.describe()})" if chosen.describe() else ""
            click.echo(f"Backend '{name}': el mensaje NO salió a Internet{where}.")

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
        if user.role != role:
            audit.record(audit.Event.ROLE_CHANGED, user=user, old=user.role, new=role, source="cli")
        user.role = role
        db.session.commit()
        click.echo(f"{user.email} → {role}")

    @app.cli.command("purge-audit")
    @click.option("--days", type=int, default=None, help="por defecto AUDIT_RETENTION_DAYS (180)")
    def purge_audit(days):
        """Borra los eventos de seguridad más antiguos que el plazo de retención."""
        days = days if days is not None else app.config["AUDIT_RETENTION_DAYS"]
        click.echo(f"Eventos borrados (anteriores a {days} días): {audit.purge_older_than(days)}")

    @app.cli.command("audit-list")
    @click.option("--limit", type=click.IntRange(1, 200), default=20)
    @click.option("--event", default=None)
    def audit_list_cmd(limit, event):
        """Muestra los últimos eventos de seguridad."""
        items, total = audit.page(limit, 0, event)
        click.echo(f"{total} eventos; los {len(items)} más recientes:")
        for item in items:
            click.echo(f"  {item['occurred_at'][:19]}  {item['event']:<17} {item['ip'] or '-':<15} {item['email_hint'] or '-'}")

    return app
