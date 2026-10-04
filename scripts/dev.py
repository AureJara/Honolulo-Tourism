#!/usr/bin/env python
"""Entorno de desarrollo SIN Docker: PostgreSQL embebido (pgserver) + los 5 servicios.

    python scripts/dev.py up                       # base de datos, migraciones y servicios
    python scripts/dev.py up --mail file --no-rate-limit   # modo de la QA automática (correo en .mail/, sin límite por IP)
    python scripts/dev.py setup-mail               # guarda tu Gmail + contraseña de aplicación (la escribes tú)
    python scripts/dev.py test-mail tu@gmail.com   # envía un correo de prueba con esa configuración
    python scripts/dev.py create-admin             # crea un administrador (pide la contraseña sin mostrarla)
    python scripts/dev.py set-role <correo> admin  # promueve (o degrada) una cuenta existente
    python scripts/dev.py migrate                  # solo aplica migraciones
    python scripts/dev.py makemigrations "mensaje" --service catalog   # migración nueva (Alembic autogenerate)

Los secretos se generan al azar en ``.env.local`` (fuera de git); no hay contraseñas ni claves en el código.
Con Docker use ``docker compose up --build`` (ver README).
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import devenv  # noqa: E402  (mismo directorio: scripts/)

ROOT = Path(__file__).resolve().parent.parent
PORTS = {"auth": 5001, "weather": 5002, "forecast": 5003, "catalog": 5004, "web": 8000}
DBS = {"auth": "auth_db", "weather": "weather_db", "forecast": "forecast_db", "catalog": "catalog_db"}
DATA_DIR = ROOT / ".pgdata"
LOG_DIR = ROOT / ".logs"


RECOVERY_WAIT_S = 180


def wait_for_recovery(timeout: float = RECOVERY_WAIT_S) -> bool:
    """Espera a que PostgreSQL termine de arrancar. Tras un cierre brusco (apagón, ventana cerrada) repasa su registro
    de transacciones y puede tardar bastante más que los 10 s que concede ``pgserver``; el proceso sigue en segundo plano."""
    from pgserver.utils import PostmasterInfo
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = PostmasterInfo.read_from_pgdata(DATA_DIR)
        if info is not None and info.is_running() and info.status == "ready":
            return True
        time.sleep(1)
    return False


def start_postgres():
    import logging

    import pgserver
    from pgserver.postgres_server import PostgresServer
    logging.getLogger("pgserver").setLevel(logging.CRITICAL)      # si tarda, pgserver vuelca todo el registro: se explica abajo
    try:
        server = pgserver.get_server(DATA_DIR, cleanup_mode="stop")
    except subprocess.TimeoutExpired:
        print("PostgreSQL se está recuperando de un cierre inesperado; esperando a que termine…", flush=True)
        if not wait_for_recovery():
            raise SystemExit("PostgreSQL no terminó de recuperarse. Espera un minuto y vuelve a ejecutar el comando.") from None
        PostgresServer._instances.pop(DATA_DIR.resolve(), None)     # el intento fallido dejó una instancia a medias
        server = pgserver.get_server(DATA_DIR, cleanup_mode="stop")  # ahora se engancha al servidor ya listo
    return server, server.get_uri()                      # postgresql://postgres:@127.0.0.1:PORT/postgres


def ensure_databases(uri: str) -> None:
    import psycopg
    from psycopg import sql
    with psycopg.connect(uri, autocommit=True) as conn:
        existing = {r[0] for r in conn.execute("SELECT datname FROM pg_database")}
        for name in DBS.values():
            if name not in existing:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def local_env(mail_mode: str = "auto") -> dict:
    """Entorno de los servicios: ``.env.local`` (secretos y SMTP) y, por encima, las variables reales del sistema."""
    values, created = devenv.ensure_dev_secrets()
    if created:
        print("Secretos de desarrollo generados al azar en .env.local (no se sube a git).")
    env = {**values, **os.environ}
    if mail_mode == "file":
        env["MAIL_BACKEND"] = "file"
    elif "MAIL_BACKEND" not in env:
        env["MAIL_BACKEND"] = "smtp" if devenv.smtp_configured(env) else "file"
    if env["MAIL_BACKEND"] == "smtp" and not env.get("MAIL_FROM") and env.get("SMTP_USER"):
        env["MAIL_FROM"] = f"Honolulo <{env['SMTP_USER']}>"       # Gmail solo envía como la cuenta autenticada
    return env


def mail_banner(env: dict) -> str:
    if env["MAIL_BACKEND"] == "smtp":
        return (f"Correo: ENVÍO REAL por {env.get('SMTP_HOST')} desde {env.get('SMTP_USER')} "
                "(los códigos llegan al correo del usuario).")
    return ("Correo: MODO ARCHIVO. Los códigos NO se envían: quedan en .mail/ (solo para desarrollo y QA).\n"
            "        Para enviarlos al correo real del usuario: python scripts/dev.py setup-mail")


def base_env(pg_uri: str, mail_mode: str = "auto") -> dict:
    sqla_base = pg_uri.replace("postgresql://", "postgresql+psycopg://", 1).rsplit("/", 1)[0]
    env = local_env(mail_mode)
    env.update({
        "APP_ENV": "development",
        "AUTH_SERVICE_URL": f"http://127.0.0.1:{PORTS['auth']}",
        "WEATHER_SERVICE_URL": f"http://127.0.0.1:{PORTS['weather']}",
        "FORECAST_SERVICE_URL": f"http://127.0.0.1:{PORTS['forecast']}",
        "CATALOG_SERVICE_URL": f"http://127.0.0.1:{PORTS['catalog']}",
        "MAIL_OUTBOX_DIR": env.get("MAIL_OUTBOX_DIR", str(ROOT / ".mail")),
        "MEDIA_ROOT": env.get("MEDIA_ROOT", str(ROOT / ".media")),
        "PYTHONUTF8": "1",
    })
    env["_SQLA_BASE"] = sqla_base
    return env


def service_env(env: dict, service: str) -> dict:
    out = dict(env)
    if service in DBS:
        out["DATABASE_URL"] = f"{env['_SQLA_BASE']}/{DBS[service]}"
    return out


def flask(service: str, env: dict, *args: str, module: str = "app:create_app") -> None:
    subprocess.run([sys.executable, "-m", "flask", "--app", module, *args],
                   cwd=ROOT / "services" / service, env=service_env(env, service), check=True)


def migrate(env: dict) -> None:
    ready = [svc for svc in DBS if (ROOT / "services" / svc / "migrations").exists()]
    for service in ready:
        print(f"→ migraciones de {service}")
        flask(service, env, "db", "upgrade")
    if "forecast" in ready:
        flask("forecast", env, "seed")      # parámetros de puntuación iniciales (idempotente)
    if "catalog" in ready:
        flask("catalog", env, "seed")       # las cuatro cascadas del diseño (idempotente)


def makemigrations(env: dict, message: str, only: str | None = None) -> None:
    for service in ([only] if only else DBS):
        svc_dir = ROOT / "services" / service / "migrations"
        if not svc_dir.exists():
            print(f"→ inicializando Alembic en {service}")
            flask(service, env, "db", "init")
        print(f"→ autogenerando migración de {service}")
        flask(service, env, "db", "migrate", "-m", message)


def wait_healthy(name: str, port: int, timeout: float = 40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def ensure_ports_free() -> None:
    """Falla si algún puerto ya responde: en Windows un servidor viejo puede compartir el puerto y
    seguir sirviendo código desactualizado sin avisar."""
    busy = []
    for name, port in PORTS.items():
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            busy.append(f"{name} (:{port})")
        except OSError:
            pass
    if busy:
        raise SystemExit("Puertos ocupados: " + ", ".join(busy) + ". ¿Sigue corriendo una ejecución anterior? "
                         "Deténla antes de iniciar (en Windows: taskkill /F /T /PID <pid>).")


def kill_tree(proc: subprocess.Popen) -> None:
    """Termina el proceso y sus hijos. En Windows el lanzador del venv crea un proceso hijo que
    ``terminate()`` no alcanza, y quedaría sirviendo en segundo plano."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()


def up(env: dict) -> None:
    ensure_ports_free()
    LOG_DIR.mkdir(exist_ok=True)
    procs: list[subprocess.Popen] = []
    try:
        for service, port in PORTS.items():
            log = open(LOG_DIR / f"{service}.log", "w", encoding="utf-8")
            procs.append(subprocess.Popen(
                [sys.executable, "-m", "flask", "--app", "wsgi", "run", "--host", "127.0.0.1",
                 "--port", str(port), "--no-reload"],
                cwd=ROOT / "services" / service, env=service_env(env, service), stdout=log, stderr=subprocess.STDOUT))
        for service, port in PORTS.items():
            ok = wait_healthy(service, port)
            print(f"  {'✔' if ok else '✘'} {service:<9} http://127.0.0.1:{port}")
        if env.get("RATE_LIMIT_ENABLED") == "0":
            print("\nAVISO: límite de peticiones por IP DESACTIVADO (--no-rate-limit): úsalo solo para la QA automática.")
        print(f"\n{mail_banner(env)}")
        print(f"Abre http://127.0.0.1:{PORTS['web']}  (logs en {LOG_DIR}). Ctrl+C para detener.")
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("Un servicio terminó; revisa los logs.")
    except KeyboardInterrupt:
        print("\nDeteniendo…")
    finally:
        for p in procs:
            kill_tree(p)
        for p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()


def setup_mail() -> None:
    """Guarda en .env.local el Gmail y su contraseña de aplicación. La contraseña se escribe aquí, sin mostrarse."""
    import getpass
    print("Configuración del correo que enviará los códigos de confirmación.\n"
          "Necesitas una contraseña de aplicación de Google (verificación en dos pasos activada):\n"
          "  https://myaccount.google.com/apppasswords\n")
    user = input("Correo Gmail remitente: ").strip()
    if "@" not in user or " " in user:
        raise SystemExit("Ese correo no parece válido.")
    password = getpass.getpass("Contraseña de aplicación (no se muestra): ").replace(" ", "")
    if len(password) < 8:
        raise SystemExit("La contraseña de aplicación es demasiado corta; no se guardó nada.")
    devenv.update_env_file({"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "SMTP_USER": user,
                            "SMTP_PASSWORD": password, "SMTP_SECURITY": "starttls"})
    print("\nGuardado en .env.local (ignorado por git). Prueba el envío con:\n"
          "  python scripts/dev.py test-mail tu_correo@gmail.com")


def test_mail(to: str) -> None:
    env = local_env()
    if env["MAIL_BACKEND"] != "smtp":
        raise SystemExit("El correo real no está configurado. Primero ejecuta: python scripts/dev.py setup-mail")
    flask("auth", env, "mail-test", to)


# ------------------------------------------------------------------- comandos
# Cada comando se registra con ``@command``. Añadir uno nuevo es escribir una función con el decorador: ``main`` no se
# modifica (principio abierto/cerrado). ``needs_database`` hace que se levante PostgreSQL y se prepare el entorno antes.
@dataclass(frozen=True)
class Command:
    name: str
    help: str
    run: Callable[[argparse.Namespace, "dict | None"], None]
    needs_database: bool = False
    configure: Callable[[argparse.ArgumentParser], None] = lambda parser: None


COMMANDS: dict[str, Command] = {}


def command(name: str, help: str, *, needs_database: bool = False, configure=None):
    def decorator(func):
        COMMANDS[name] = Command(name, help, func, needs_database, configure or (lambda parser: None))
        return func
    return decorator


def _configure_up(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mail", choices=["auto", "file"], default="auto",
                        help="auto: SMTP si está configurado; file: deja los correos en .mail/")
    parser.add_argument("--no-rate-limit", action="store_true",
                        help="desactiva el límite de peticiones por IP (solo para la QA automática, que hace cientos de "
                             "registros desde una misma IP)")


@command("up", "migra la base de datos y levanta los servicios", needs_database=True, configure=_configure_up)
def _cmd_up(args, env) -> None:
    if args.no_rate_limit:
        env["RATE_LIMIT_ENABLED"] = "0"
    migrate(env)
    up(env)


@command("migrate", "aplica las migraciones", needs_database=True)
def _cmd_migrate(args, env) -> None:
    migrate(env)


def _configure_makemigrations(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("message", nargs="?", default="cambios de esquema")
    parser.add_argument("--service", choices=list(DBS), help="solo este servicio")


@command("makemigrations", "genera una migración nueva (Alembic)", needs_database=True,
         configure=_configure_makemigrations)
def _cmd_makemigrations(args, env) -> None:
    if args.service:
        migrate(env)
    makemigrations(env, args.message, args.service)


@command("setup-mail", "guarda el Gmail y su contraseña de aplicación en .env.local")
def _cmd_setup_mail(args, env) -> None:
    setup_mail()


@command("test-mail", "envía un correo de prueba con la configuración actual",
         configure=lambda parser: parser.add_argument("to"))
def _cmd_test_mail(args, env) -> None:
    test_mail(args.to)


@command("create-admin", "crea un administrador (la contraseña se pide sin mostrarla)", needs_database=True)
def _cmd_create_admin(args, env) -> None:
    flask("auth", env, "create-admin")


def _configure_set_role(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("email")
    parser.add_argument("role", choices=["user", "admin"])


@command("set-role", "cambia el rol de una cuenta existente", needs_database=True, configure=_configure_set_role)
def _cmd_set_role(args, env) -> None:
    flask("auth", env, "set-role", args.email, args.role)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):          # la consola de Windows puede ser cp1252 al canalizar la salida
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    for cmd in COMMANDS.values():
        cmd.configure(sub.add_parser(cmd.name, help=cmd.help))
    args = parser.parse_args()
    if args.cmd is None:                              # sin comando: levantar el sistema
        args = parser.parse_args(["up"])
    cmd = COMMANDS[args.cmd]

    env = None
    if cmd.needs_database:
        server, uri = start_postgres()                # ``server`` debe seguir vivo mientras dure el comando
        print(f"PostgreSQL embebido: {uri}")
        ensure_databases(uri)
        env = base_env(uri, getattr(args, "mail", "auto"))
    cmd.run(args, env)


if __name__ == "__main__":
    main()
