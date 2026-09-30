#!/usr/bin/env python
"""Entorno de desarrollo SIN Docker: PostgreSQL embebido (pgserver) + los 4 servicios.

    python scripts/dev.py up                       # base de datos, migraciones y servicios
    python scripts/dev.py migrate                  # solo aplica migraciones
    python scripts/dev.py makemigrations "mensaje" --service catalog   # migración nueva (Alembic autogenerate)

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTS = {"auth": 5001, "weather": 5002, "forecast": 5003, "catalog": 5004, "web": 8000}
DBS = {"auth": "auth_db", "weather": "weather_db", "forecast": "forecast_db", "catalog": "catalog_db"}
DATA_DIR = ROOT / ".pgdata"
LOG_DIR = ROOT / ".logs"


def start_postgres():
    import pgserver
    server = pgserver.get_server(DATA_DIR, cleanup_mode="stop")
    return server, server.get_uri()                      # postgresql://postgres:@127.0.0.1:PORT/postgres


def ensure_databases(uri: str) -> None:
    import psycopg
    from psycopg import sql
    with psycopg.connect(uri, autocommit=True) as conn:
        existing = {r[0] for r in conn.execute("SELECT datname FROM pg_database")}
        for name in DBS.values():
            if name not in existing:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def base_env(pg_uri: str) -> dict:
    sqla_base = pg_uri.replace("postgresql://", "postgresql+psycopg://", 1).rsplit("/", 1)[0]
    env = dict(os.environ)
    env.update({
        "APP_ENV": "development",
        "JWT_SECRET_KEY": env.get("JWT_SECRET_KEY", "dev-secret-change-me-dev-secret-change-me"),
        "INTERNAL_API_TOKEN": env.get("INTERNAL_API_TOKEN", "dev-internal-token"),
        "AUTH_SERVICE_URL": f"http://127.0.0.1:{PORTS['auth']}",
        "WEATHER_SERVICE_URL": f"http://127.0.0.1:{PORTS['weather']}",
        "FORECAST_SERVICE_URL": f"http://127.0.0.1:{PORTS['forecast']}",
        "CATALOG_SERVICE_URL": f"http://127.0.0.1:{PORTS['catalog']}",
        # Correo de desarrollo: los mensajes (con el código de confirmación) quedan en .mail/ en vez de enviarse.
        "MAIL_BACKEND": env.get("MAIL_BACKEND", "file"),
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
        print(f"\nAbre http://127.0.0.1:{PORTS['web']}  (logs en {LOG_DIR}). Ctrl+C para detener.")
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


def main() -> None:
    for stream in (sys.stdout, sys.stderr):          # la consola de Windows puede ser cp1252 al canalizar la salida
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("up")
    sub.add_parser("migrate")
    mm = sub.add_parser("makemigrations")
    mm.add_argument("message", nargs="?", default="cambios de esquema")
    mm.add_argument("--service", choices=list(DBS), help="solo este servicio")
    args = parser.parse_args()

    server, uri = start_postgres()
    print(f"PostgreSQL embebido: {uri}")
    ensure_databases(uri)
    env = base_env(uri)
    if args.cmd == "makemigrations":
        migrate(env) if args.service else None
        makemigrations(env, args.message, args.service)
    elif args.cmd == "migrate":
        migrate(env)
    else:
        migrate(env)
        up(env)


if __name__ == "__main__":
    main()
