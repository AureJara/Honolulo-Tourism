"""URL de PostgreSQL con otra base de datos: debe conservar el socket Unix de Linux y macOS.

En Windows ``pgserver`` usa TCP (``postgresql://postgres:@127.0.0.1:5432/postgres``) y cortar la URL por la última barra
bastaba. En Linux entrega ``postgresql://postgres:@/postgres?host=/ruta/del/socket``: la ruta también tiene barras, y la
versión anterior perdía el ``?host=…`` y la conexión fallaba (143 de 149 pruebas de ``auth`` daban error). Sin esto las
pruebas no pueden correr en integración continua (Linux)."""

import sys
from pathlib import Path

import pytest

from honolulo_common.testing import with_database

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import dev  # noqa: E402

CASES = [
    ("postgresql://postgres:@127.0.0.1:5432/postgres", "auth_x", "postgresql://postgres:@127.0.0.1:5432/auth_x"),
    ("postgresql+psycopg://u:p@db.example.org:5432/postgres", "w1", "postgresql+psycopg://u:p@db.example.org:5432/w1"),
    ("postgresql://postgres:@/postgres?host=/tmp/pg/sock", "auth_x", "postgresql://postgres:@/auth_x?host=/tmp/pg/sock"),
    ("postgresql+psycopg://postgres:@/postgres?host=/home/u/.pgdata&port=5433", "f1",
     "postgresql+psycopg://postgres:@/f1?host=/home/u/.pgdata&port=5433"),
]


@pytest.mark.parametrize("url, name, expected", CASES)
def test_with_database_changes_only_the_database_name(url, name, expected):
    assert with_database(url, name) == expected


@pytest.mark.parametrize("url, name, expected", CASES)
def test_dev_script_uses_the_same_rule(url, name, expected):
    """``scripts/dev.py`` repite la función (puede arrancar sin la librería instalada): no deben divergir."""
    assert dev.with_database(url, name) == expected


def test_dev_script_builds_each_service_url_from_the_unix_socket_server():
    env = {"_SQLA_BASE": "postgresql+psycopg://postgres:@/postgres?host=/tmp/pg/sock"}
    assert dev.service_env(env, "catalog")["DATABASE_URL"] == "postgresql+psycopg://postgres:@/catalog_db?host=/tmp/pg/sock"
