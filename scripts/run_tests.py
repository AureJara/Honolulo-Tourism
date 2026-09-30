#!/usr/bin/env python
"""Ejecuta todas las suites de pruebas (librería común + cada microservicio).

Las pruebas usan PostgreSQL real: si no define TEST_DATABASE_URL (servidor PostgreSQL, p. ej.
``postgresql://postgres:pass@localhost:5432/postgres``) se levanta uno embebido con pgserver.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = ["libs/honolulo_common", "services/auth", "services/weather", "services/forecast", "services/catalog", "services/web"]


def main() -> int:
    failed = []
    for suite in SUITES:
        print(f"\n=== {suite} ===", flush=True)
        code = subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *sys.argv[1:]],
                               cwd=ROOT / suite)
        if code:
            failed.append(suite)
    print("\nResultado:", "TODO OK" if not failed else f"fallaron: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
