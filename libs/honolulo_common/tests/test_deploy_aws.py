"""Archivos del despliegue en AWS (deploy/aws): lo que se puede comprobar sin Docker ni AWS.

Los contenedores nunca se han construido en el equipo de desarrollo; estas pruebas vigilan lo que sí se puede verificar:
que solo Caddy quede abierto a Internet, que el script de secretos funcione y no sobrescriba nada, y que todo lo que el
script rellena exista en ``.env.example``."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AWS = ROOT / "deploy" / "aws"
SECRETS = ("JWT_SECRET_KEY", "INTERNAL_API_TOKEN", "WEB_SECRET_KEY", "POSTGRES_PASSWORD", "AUTH_DB_PASSWORD",
           "WEATHER_DB_PASSWORD", "FORECAST_DB_PASSWORD", "CATALOG_DB_PASSWORD")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def env_lines(text: str) -> dict[str, str]:
    return {k: v for k, _, v in (line.partition("=") for line in text.splitlines() if re.match(r"^[A-Z_]+=", line))}


# ------------------------------------------------------------------------------ Caddy
def test_caddy_takes_the_domain_from_the_environment_never_hardcoded():
    caddyfile = read(AWS / "Caddyfile")
    assert "{$DOMAIN}" in caddyfile and "reverse_proxy web:8000" in caddyfile
    assert not re.search(r"duckdns|\.com|\.pe\b", caddyfile.replace("Let's Encrypt", ""))


def test_caddy_cuts_slow_connections_and_oversized_requests():
    """Lo que gunicorn solo no aguanta (ver docs: conexiones lentas)."""
    caddyfile = read(AWS / "Caddyfile")
    for directive in ("read_header", "read_body", "idle", "max_header_size", "max_size 6MB"):
        assert directive in caddyfile, directive


# --------------------------------------------------------------------------- compose
def test_only_caddy_is_opened_to_the_internet():
    code = "\n".join(line for line in read(AWS / "docker-compose.aws.yml").splitlines() if not line.lstrip().startswith("#"))
    assert set(re.findall(r'"(\d+:\d+)"', code)) == {"80:80", "443:443"}
    for port in ("5001", "5002", "5003", "5004", "5432", "8000"):
        assert port not in code, port


def test_the_compose_override_requires_a_domain_and_keeps_the_certificates():
    compose = read(AWS / "docker-compose.aws.yml")
    assert "DOMAIN: ${DOMAIN:?" in compose and "caddy_data:/data" in compose
    assert (ROOT / "deploy" / "aws" / "Caddyfile").exists() and "./deploy/aws/Caddyfile" in compose


def test_the_main_compose_still_hides_the_database_and_internal_services():
    compose = read(ROOT / "docker-compose.yml")
    assert re.findall(r'ports: \["(\d+:\d+)"\]', compose) == ["8000:8000"]       # solo la web publica un puerto


# ---------------------------------------------------------------------------- .env
def test_everything_the_script_fills_exists_in_env_example():
    """Si faltara una línea, el ``sed`` del script no haría nada y el servidor arrancaría con un valor vacío."""
    example = env_lines(read(ROOT / ".env.example"))
    for name in (*SECRETS, "DOMAIN", "TRUSTED_PROXY_HOPS"):
        assert name in example, name
    assert example["DOMAIN"].split("#")[0].strip() == ""                          # sin dominio por defecto


def test_the_script_never_writes_a_password_for_the_mail():
    script = read(AWS / "generar-env.sh")
    assert "SMTP_PASSWORD=" not in script and "SMTP_USER=" not in script
    assert "no se sobrescribe" in script.lower()


def find_bash():
    candidates = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe", shutil.which("bash")]
    for candidate in candidates:
        if candidate and Path(candidate).exists() and "system32" not in candidate.lower():     # bash.exe de WSL: no
            probe = subprocess.run([candidate, "-c", "command -v openssl && command -v sed && echo ok"],
                                   capture_output=True, text=True)
            if probe.returncode == 0 and probe.stdout.strip().endswith("ok"):
                return candidate
    return None


@pytest.fixture()
def project(tmp_path):
    bash = find_bash()
    if bash is None:
        pytest.skip("hace falta bash con openssl y sed")
    (tmp_path / "deploy" / "aws").mkdir(parents=True)
    shutil.copy(AWS / "generar-env.sh", tmp_path / "deploy" / "aws" / "generar-env.sh")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")

    def run(*args):
        script = (tmp_path / "deploy" / "aws" / "generar-env.sh").as_posix()
        return subprocess.run([bash, script, *args], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    run.root = tmp_path
    return run


def test_the_script_generates_distinct_random_secrets_and_prints_none_of_them(project):
    result = project("honolulo.duckdns.org")
    assert result.returncode == 0, result.stderr
    values = env_lines(read(project.root / ".env"))
    secrets = [values[name] for name in SECRETS]
    assert all(re.fullmatch(r"[0-9a-f]{64}", s) for s in secrets) and len(set(secrets)) == len(SECRETS)
    assert values["DOMAIN"] == "honolulo.duckdns.org" and values["TRUSTED_PROXY_HOPS"] == "1"
    assert values["COMPOSE_FILE"] == "docker-compose.yml:deploy/aws/docker-compose.aws.yml"
    assert values["SMTP_PASSWORD"] == "" and values["SMTP_USER"] == ""               # el correo lo escribe la persona
    assert not any(s in result.stdout + result.stderr for s in secrets)               # nada de lo secreto se imprime
    if sys.platform != "win32":
        assert (project.root / ".env").stat().st_mode & 0o777 == 0o600


def test_the_script_never_overwrites_an_existing_env(project):
    (project.root / ".env").write_text("ESTO=no_se_toca\n", encoding="utf-8")
    result = project("honolulo.duckdns.org")
    assert result.returncode != 0 and "no se sobrescribe" in result.stderr.lower()
    assert read(project.root / ".env") == "ESTO=no_se_toca\n"


@pytest.mark.parametrize("domain", ["", "a;rm -rf x", "$(whoami)", "-abc", "mi dominio.com", "a|b", "x&y"])
def test_the_script_refuses_a_domain_that_could_break_the_sed_command(project, domain):
    result = project(domain) if domain else project()
    assert result.returncode != 0 and not (project.root / ".env").exists()


# -------------------------------------------------------------------------------- guía
def test_the_guide_is_honest_and_keeps_the_dangerous_ports_closed():
    guide = read(ROOT / "docs" / "despliegue-aws.md")
    assert "NO se ha probado" in guide and "nunca se han" in guide                       # dice lo que no se probó
    assert "No abras" in guide and "5432" in guide and "8000" in guide                  # puertos que no se abren
    assert "generar-env.sh" in guide and "docker compose up -d --build" in guide
    assert not re.search(r"AKIA[0-9A-Z]{16}|BEGIN (RSA )?PRIVATE KEY", guide)             # ninguna clave de AWS ni .pem


def test_deploy_files_stay_out_of_the_docker_images():
    ignored = read(ROOT / ".dockerignore").split()
    assert "deploy" in ignored and "docs" in ignored
