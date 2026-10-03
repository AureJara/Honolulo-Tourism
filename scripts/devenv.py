"""Configuración local de desarrollo guardada en ``.env.local`` (ignorado por git).

Ningún secreto vive en el código: los secretos de firma se generan al azar la primera vez y las credenciales del
correo (contraseña de aplicación de Gmail) las escribe la propia persona en su terminal con ``dev.py setup-mail``.
El archivo nunca se imprime ni se sube al repositorio. En producción se usan variables de entorno o el gestor de
secretos del hosting, no este archivo.
"""

from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env.local"
GENERATED = ("JWT_SECRET_KEY", "INTERNAL_API_TOKEN", "WEB_SECRET_KEY")
SMTP_KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_SECURITY")

_HEADER = (
    "# Configuración LOCAL de Honolulo (no se sube a git: está en .gitignore).\n"
    "# Los secretos se generaron al azar. No los compartas ni los pegues en el código.\n"
)


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_file(values: dict[str, str], path: Path = ENV_FILE) -> None:
    body = "".join(f"{key}={value}\n" for key, value in values.items())
    path.write_text(_HEADER + body, encoding="utf-8")


def update_env_file(updates: dict[str, str], path: Path = ENV_FILE) -> None:
    values = read_env_file(path)
    values.update(updates)
    write_env_file(values, path)


def ensure_dev_secrets(path: Path = ENV_FILE) -> tuple[dict[str, str], bool]:
    """Devuelve los valores del archivo, creando los secretos que falten. El segundo valor indica si se creó alguno."""
    values = read_env_file(path)
    created = False
    for key in GENERATED:
        if len(values.get(key, "")) < 32:
            values[key] = secrets.token_urlsafe(48)
            created = True
    if created:
        write_env_file(values, path)
    return values, created


def smtp_configured(values: dict[str, str]) -> bool:
    return all(values.get(key) for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
