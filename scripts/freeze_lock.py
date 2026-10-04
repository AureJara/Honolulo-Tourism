#!/usr/bin/env python
"""Regenera ``requirements-lock.txt``: las versiones EXACTAS (directas y transitivas) con las que se prueba y se despliega.

    python scripts/freeze_lock.py            # reescribe requirements-lock.txt
    python scripts/freeze_lock.py --check    # no escribe: falla si el archivo ya no cumple los requirements

Instala en un entorno temporal limpio los ``requirements.txt`` de todos los servicios más la librería común y congela lo
que pip resolvió. Los Dockerfile lo usan como restricción (``pip install -c requirements-lock.txt ...``), así que cada
despliegue instala exactamente lo mismo que se probó. Después de regenerarlo hay que correr las pruebas.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements-lock.txt"
SERVICES = ("auth", "weather", "forecast", "catalog", "web")
HEADER = (
    "# Versiones PROBADAS de todas las dependencias (directas y transitivas) de los servicios.\n"
    "# Los Dockerfile las usan como restricción (pip install -c requirements-lock.txt ...) para que cada despliegue\n"
    "# instale exactamente lo mismo que se probó. No se edita a mano: python scripts/freeze_lock.py\n"
    "# Dependabot propone las actualizaciones; tras aceptarlas hay que correr las pruebas.\n"
)


def _python(venv: Path) -> str:
    return str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def requirement_args() -> list[str]:
    args: list[str] = []
    for service in SERVICES:
        args += ["-r", str(ROOT / "services" / service / "requirements.txt")]
    return args


def resolve() -> list[str]:
    """Instala todo en un entorno limpio y devuelve las líneas ``paquete==versión`` ordenadas."""
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        py = _python(venv)
        subprocess.run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *requirement_args(),
                        str(ROOT / "libs" / "honolulo_common")], check=True)
        frozen = subprocess.run([py, "-m", "pip", "freeze", "--exclude", "honolulo-common"], check=True,
                                capture_output=True, text=True).stdout
    return sorted((line.strip() for line in frozen.splitlines() if line.strip()), key=str.lower)


def check() -> int:
    """El archivo actual debe poder instalarse junto a los requirements sin cambiar ninguna versión."""
    if not LOCK.exists():
        print("Falta requirements-lock.txt")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        result = subprocess.run([_python(venv), "-m", "pip", "install", "--dry-run", "--quiet", "--disable-pip-version-check",
                                 "-c", str(LOCK), *requirement_args(), str(ROOT / "libs" / "honolulo_common")],
                                capture_output=True, text=True)
    if result.returncode:
        print("requirements-lock.txt no es compatible con los requirements:\n" + result.stderr[-1500:])
    else:
        print("requirements-lock.txt es compatible con los requirements.")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    if parser.parse_args().check:
        return check()
    lines = resolve()
    with open(LOCK, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(HEADER + "\n".join(lines) + "\n")
    print(f"requirements-lock.txt actualizado: {len(lines)} paquetes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
