#!/usr/bin/env python
"""Revisa que no haya contraseñas, claves ni tokens escritos en el código (ni en la configuración).

    python scripts/check_secrets.py            # revisa los archivos versionados; sale con 1 si encuentra algo

Qué busca (en código y configuración; las carpetas ``tests/`` y ``qa/`` usan valores de prueba y se omiten):
  * asignaciones de contraseñas/secretos/tokens con un valor literal (``PASSWORD = "..."``);
  * valores por defecto de ``os.getenv("..._SECRET/_PASSWORD/_TOKEN/_KEY", "valor")``;
  * URLs con usuario y contraseña (``postgresql://usuario:clave@host``);
  * formatos conocidos de claves (AWS, GitHub, Slack) y claves privadas PEM.

Nunca imprime el valor encontrado, solo el archivo, la línea y el motivo. Para un falso positivo deliberado, añade
``# noqa: secret`` en esa línea.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCANNED = {".py", ".yml", ".yaml", ".sh", ".js", ".html", ".toml", ".ini", ".cfg", ".json", ".example", ".md", ".txt"}
SKIP_DIRS = {"tests", "qa", ".venv", ".git", "node_modules", "__pycache__", ".pgdata", ".logs", ".mail", ".media"}
SKIP_FILES = {"check_secrets.py"}
NAME = r"[\w.-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)[\w.-]*"
PLACEHOLDER = re.compile(r"^(?:\$\{.*|\$\(.*|\{\{.*|<[^>]+>.*|%\(.*|%s.*|x{4,}|\.\.\.)$", re.I)   # marcadores de posición

RULES = [
    ("valor literal en una contraseña, secreto o token",
     re.compile(rf"""(?i)\b{NAME}\s*[:=]\s*["']([^"'\s]{{8,}})["']""")),
    ("valor por defecto de un secreto en os.getenv",
     re.compile(r"""(?i)getenv\(\s*["'][^"']*(?:password|secret|token|key)["']\s*,\s*["']([^"']{4,})["']""")),
    ("URL con usuario y contraseña",
     re.compile(r"""[a-z][a-z0-9+.-]*://[^/\s:@"']+:([^@\s/"']{3,})@""")),
    ("clave con formato conocido",
     re.compile(r"(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")),
]


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        paths = [ROOT / line for line in out.splitlines() if line]
        # los archivos nuevos todavía sin versionar también cuentan
        extra = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout
        paths += [ROOT / line for line in extra.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in ROOT.rglob("*") if p.is_file()]
    return [p for p in paths if p.suffix in SCANNED and p.name not in SKIP_FILES
            and not (SKIP_DIRS & set(p.relative_to(ROOT).parts)) and p.exists()]


def scan_text(text: str) -> list[tuple[int, str]]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        if "noqa: secret" in line:
            continue
        for reason, rule in RULES:
            for match in rule.finditer(line):
                value = match.group(match.lastindex or 0)
                if PLACEHOLDER.search(value):
                    continue
                findings.append((number, reason))
                break
    return findings


def scan(paths: list[Path] | None = None) -> list[str]:
    report = []
    for path in paths if paths is not None else tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, reason in scan_text(text):
            report.append(f"{path.relative_to(ROOT)}:{number}: {reason}")
    return report


def main() -> int:
    report = scan()
    if report:
        print("Posibles secretos en el código (el valor no se muestra):")
        print("\n".join(f"  {item}" for item in report))
        return 1
    print("Sin secretos escritos en el código.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
