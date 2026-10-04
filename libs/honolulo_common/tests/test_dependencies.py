"""Higiene de dependencias: lo declarado se usa, lo usado está declarado, y las versiones probadas quedan fijadas."""

import ast
import re
import sys
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[3]
SERVICES = ("auth", "weather", "forecast", "catalog", "web")

# Nombre del paquete en pip -> módulo que se importa en el código.
IMPORT_NAME = {"flask": "flask", "flask-sqlalchemy": "flask_sqlalchemy", "flask-migrate": "flask_migrate",
               "sqlalchemy": "sqlalchemy", "marshmallow": "marshmallow", "pyjwt": "jwt", "requests": "requests",
               "apscheduler": "apscheduler", "pillow": "PIL"}
# Se necesitan para funcionar aunque ningún archivo los importe: servidor de producción, controlador de la base
# (SQLAlchemy lo carga por la URL ``postgresql+psycopg://``) y datos de zonas horarias.
RUNTIME_ONLY = {"gunicorn", "psycopg", "tzdata"}
# Módulos que llegan con otro paquete declarado (Flask trae werkzeug, click y jinja2).
BUNDLED = {"werkzeug", "click", "jinja2", "markupsafe", "itsdangerous"}


def requirements(path: Path) -> dict[str, Requirement]:
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith(("-", "git+")):
            req = Requirement(line)
            found[canonicalize_name(req.name)] = req
    return found


def imported_modules(directory: Path, extra=()) -> set[str]:
    modules = set()
    for path in [*directory.rglob("*.py"), *extra]:
        if "__pycache__" in path.parts or "migrations" in path.parts or "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules


LIB_DEPS = {"flask", "pyjwt", "requests", "tzdata"}                      # lo que declara libs/honolulo_common
LIB_MODULES = {IMPORT_NAME[name] for name in LIB_DEPS if name in IMPORT_NAME}


@pytest.mark.parametrize("service", SERVICES)
def test_every_declared_package_is_used_by_the_service(service):
    declared = set(requirements(ROOT / "services" / service / "requirements.txt"))
    used = imported_modules(ROOT / "services" / service / "app", [ROOT / "services" / service / "wsgi.py"])
    unused = [name for name in declared - RUNTIME_ONLY if IMPORT_NAME[name] not in used]
    assert not unused, f"{service} declara paquetes que no usa (los trae la librería común o sobran): {unused}"


@pytest.mark.parametrize("service", SERVICES)
def test_every_third_party_import_is_declared(service):
    declared_modules = {IMPORT_NAME[n] for n in requirements(ROOT / "services" / service / "requirements.txt") if n in IMPORT_NAME}
    stdlib = set(sys.stdlib_module_names)
    third_party = {m for m in imported_modules(ROOT / "services" / service / "app", [ROOT / "services" / service / "wsgi.py"])
                   if m not in stdlib and m not in {"app", "honolulo_common"}}
    undeclared = third_party - declared_modules - LIB_MODULES - BUNDLED
    assert not undeclared, f"{service} importa paquetes que no declara: {sorted(undeclared)}"


def test_the_common_library_declares_what_it_imports():
    declared = LIB_MODULES                                                  # lo que declara su pyproject.toml
    stdlib = set(sys.stdlib_module_names)
    modules = {m for m in imported_modules(ROOT / "libs" / "honolulo_common" / "honolulo_common")
               if m not in stdlib and m != "honolulo_common"}
    # ``psycopg`` y ``sqlalchemy`` solo los usa el ayudante de pruebas (testing.py), que no se instala en producción.
    assert modules - declared - BUNDLED - {"psycopg", "sqlalchemy", "pgserver"} == set(), sorted(modules)


def test_the_lock_file_pins_every_requirement_to_a_version_that_satisfies_it():
    lock = {canonicalize_name(r.name): next(iter(r.specifier)).version
            for r in requirements(ROOT / "requirements-lock.txt").values()}
    assert len(lock) >= 20
    for service in SERVICES:
        for name, req in requirements(ROOT / "services" / service / "requirements.txt").items():
            assert name in lock, f"{name} ({service}) no está fijado en requirements-lock.txt"
            assert lock[name] in req.specifier, f"{name}=={lock[name]} no cumple {req.specifier} ({service})"


def test_every_lock_entry_is_an_exact_pin():
    for line in (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            assert re.fullmatch(r"[A-Za-z0-9_.\-\[\]]+==[0-9][A-Za-z0-9.\-+!]*", line.strip()), line


@pytest.mark.parametrize("service", SERVICES)
def test_dockerfiles_install_exactly_the_tested_versions(service):
    dockerfile = (ROOT / "services" / service / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements-lock.txt /tmp/requirements-lock.txt" in dockerfile
    assert re.search(r"pip install -c /tmp/requirements-lock\.txt -r requirements\.txt /opt/honolulo_common", dockerfile)
    assert "USER app" in dockerfile                                       # y siguen sin correr como administrador


def test_no_requirement_is_left_without_an_upper_bound_except_data_packages():
    for service in SERVICES:
        for name, req in requirements(ROOT / "services" / service / "requirements.txt").items():
            if name != "tzdata":
                assert "<" in str(req.specifier), f"{name} ({service}) no tiene cota superior"


def test_dockerignore_keeps_secrets_and_local_data_out_of_the_images():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for must in (".git", ".venv", ".env", ".env.*", ".pgdata", ".logs", ".mail", ".media", "**/tests", "qa"):
        assert must in ignored, must
    assert "libs" not in ignored and "services" not in ignored and "requirements-lock.txt" not in ignored


def test_dependabot_covers_the_lock_file_and_every_dockerfile():
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert "package-ecosystem: pip" in config and 'directory: "/"' in config
    for service in SERVICES:
        assert f'directory: "/services/{service}"' in config
