"""Higiene del repositorio: ninguna contraseña ni clave en el código, secretos locales fuera de git y servicios
que se niegan a arrancar sin secretos fuertes."""

import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask

from honolulo_common.app_setup import MIN_SECRET_LENGTH, require_secrets

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import check_secrets  # noqa: E402
import devenv  # noqa: E402


# ----------------------------------------------------------- el escáner de secretos
@pytest.mark.parametrize("line", [
    'DB_PASSWORD = "hunter2hunter2"',
    'api_token: "abcdef123456"',
    "SECRET_KEY='una-clave-larga-escrita-a-mano'",
    'JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me")',
    'x = os.getenv("INTERNAL_API_TOKEN", "dev-internal-token")',
    'url = "postgresql://admin:SuperClave99@db.interno:5432/app"',
    "token = 'ghp_" + "a" * 36 + "'",
    "-----BEGIN RSA PRIVATE KEY-----",
])
def test_scanner_detects_hardcoded_secrets(line):
    assert check_secrets.scan_text(line), line


@pytest.mark.parametrize("line", [
    'SECRET_KEY = os.getenv("WEB_SECRET_KEY", "")',
    'PASSWORD_HASH_METHOD = os.getenv("PASSWORD_HASH_METHOD", "scrypt")',
    'PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "10"))',
    'DATABASE_URL: postgresql+psycopg://auth_svc:${AUTH_DB_PASSWORD:?defina AUTH_DB_PASSWORD}@postgres:5432/auth_db',
    "export url=postgresql://<usuario>:<clave>@localhost:5432/postgres",
    'password = "{{ form.password }}"',
    'JWT_SECRET_KEY=',
    'SMTP_PASSWORD = "tu_contraseña_aquí"  # noqa: secret',
    'label = "Contraseña"',
    'hint = "Mínimo 10 caracteres"',
])
def test_scanner_ignores_placeholders_and_safe_code(line):
    assert check_secrets.scan_text(line) == [], line


def test_scanner_never_prints_the_secret_value():
    report = check_secrets.scan_text('DB_PASSWORD = "hunter2hunter2"')
    assert report and "hunter2" not in str(report)


def test_the_repository_has_no_hardcoded_secrets():
    assert check_secrets.scan() == []


def test_the_scanner_command_exits_cleanly():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_secrets.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("name", [".env", ".env.local", ".dev-credentials.local", ".mail/codigo.txt", ".logs/web.log",
                                  ".pgdata/base", ".media/foto.webp"])
def test_local_secret_and_data_files_are_ignored_by_git(name):
    try:
        result = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT)
    except OSError:
        pytest.skip("git no está disponible")
    if result.returncode == 128:
        pytest.skip("no es un repositorio git")
    assert result.returncode == 0, f"{name} no está en .gitignore"


def test_env_example_has_no_values_for_secrets():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and any(word in key.upper() for word in ("PASSWORD", "SECRET", "TOKEN")):
            assert value.split("#")[0].strip() == "", f"{key} trae un valor en .env.example"


# ------------------------------------------------------------ secretos obligatorios
def _app(**config):
    app = Flask(__name__)
    app.config.update(config)
    return app


def test_services_refuse_to_start_without_a_secret():
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY no está configurado"):
        require_secrets(_app(), "JWT_SECRET_KEY")
    with pytest.raises(RuntimeError, match="no está configurado"):
        require_secrets(_app(JWT_SECRET_KEY=""), "JWT_SECRET_KEY")


@pytest.mark.parametrize("value", ["secret", "changeme", "dev-secret-change-me", "dev-internal-token", "x" * 31,
                                   "Password", "ADMIN"])
def test_weak_secrets_are_rejected_outside_tests(value):
    with pytest.raises(RuntimeError, match="demasiado débil"):
        require_secrets(_app(JWT_SECRET_KEY=value), "JWT_SECRET_KEY")


def test_strong_secrets_are_accepted_and_tests_may_use_short_ones():
    require_secrets(_app(JWT_SECRET_KEY="k" * MIN_SECRET_LENGTH), "JWT_SECRET_KEY")
    require_secrets(_app(TESTING=True, JWT_SECRET_KEY="corto"), "JWT_SECRET_KEY")
    with pytest.raises(RuntimeError):                      # ni siquiera en pruebas se admite vacío
        require_secrets(_app(TESTING=True, JWT_SECRET_KEY=""), "JWT_SECRET_KEY")


def test_the_error_tells_how_to_generate_a_secret():
    with pytest.raises(RuntimeError, match="token_urlsafe"):
        require_secrets(_app(), "WEB_SECRET_KEY")


# ------------------------------------------------------------- .env.local de desarrollo
def test_dev_secrets_are_generated_randomly_once(tmp_path):
    env_file = tmp_path / ".env.local"
    first, created = devenv.ensure_dev_secrets(env_file)
    assert created and set(devenv.GENERATED) <= set(first)
    assert all(len(first[k]) >= MIN_SECRET_LENGTH for k in devenv.GENERATED)
    assert len({first[k] for k in devenv.GENERATED}) == len(devenv.GENERATED)          # todos distintos
    second, created_again = devenv.ensure_dev_secrets(env_file)
    assert created_again is False and second == first                                   # no cambian entre arranques
    other, _ = devenv.ensure_dev_secrets(tmp_path / "otro.env")
    assert other["JWT_SECRET_KEY"] != first["JWT_SECRET_KEY"]                           # y no se repiten entre equipos


def test_updating_the_env_file_keeps_existing_values(tmp_path):
    env_file = tmp_path / ".env.local"
    values, _ = devenv.ensure_dev_secrets(env_file)
    devenv.update_env_file({"SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "yo@gmail.com", "SMTP_PASSWORD": "abcdwxyzabcdwxyz"},
                           env_file)
    after = devenv.read_env_file(env_file)
    assert after["JWT_SECRET_KEY"] == values["JWT_SECRET_KEY"] and after["SMTP_USER"] == "yo@gmail.com"
    assert devenv.smtp_configured(after) and not devenv.smtp_configured(values)


def test_env_file_parsing_handles_comments_quotes_and_blank_lines(tmp_path):
    env_file = tmp_path / "x.env"
    env_file.write_text('# comentario\n\nA=1\nB="dos"\nC=\'tres\'\nsin_igual\nD = cuatro\n', encoding="utf-8")
    assert devenv.read_env_file(env_file) == {"A": "1", "B": "dos", "C": "tres", "D": "cuatro"}
    assert devenv.read_env_file(tmp_path / "no-existe.env") == {}


# ------------------------------------------------ comandos de dev.py: se registran, no se enumeran en un if
def test_dev_commands_are_registered_in_a_table(monkeypatch):
    import dev
    assert {"up", "migrate", "makemigrations", "setup-mail", "test-mail", "create-admin", "set-role"} <= set(dev.COMMANDS)
    assert dev.COMMANDS["up"].needs_database and not dev.COMMANDS["setup-mail"].needs_database


def test_a_new_dev_command_needs_no_change_to_main(monkeypatch):
    import dev
    monkeypatch.setattr(dev, "COMMANDS", dict(dev.COMMANDS))
    ran = []

    @dev.command("saludo", "demostración", configure=lambda parser: parser.add_argument("nombre"))
    def _saludo(args, env):
        ran.append((args.nombre, env))

    monkeypatch.setattr(sys, "argv", ["dev.py", "saludo", "Ana"])
    dev.main()                                         # no necesita base de datos: no se levanta PostgreSQL
    assert ran == [("Ana", None)]


def test_dev_without_a_command_defaults_to_up(monkeypatch):
    import dev
    monkeypatch.setattr(dev, "COMMANDS", dict(dev.COMMANDS))
    calls = []
    original = dev.COMMANDS["up"]
    monkeypatch.setitem(dev.COMMANDS, "up", dev.Command("up", original.help, lambda a, e: calls.append(a.mail),
                                                        needs_database=False, configure=original.configure))
    monkeypatch.setattr(sys, "argv", ["dev.py"])
    dev.main()
    assert calls == ["auto"]


# ----------------------------------------- PostgreSQL embebido: espera la recuperación tras un cierre brusco
def test_start_postgres_waits_for_crash_recovery_and_then_attaches(monkeypatch):
    import dev
    import pgserver
    from pgserver.postgres_server import PostgresServer

    calls, fake = [], type("S", (), {"get_uri": lambda self: "postgresql://postgres:@127.0.0.1:1/postgres"})()

    def get_server(path, cleanup_mode):
        calls.append(cleanup_mode)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("pg_ctl", 10)        # tardó más de 10 s: se estaba recuperando
        return fake

    monkeypatch.setattr(pgserver, "get_server", get_server)
    monkeypatch.setattr(dev, "wait_for_recovery", lambda: True)
    stale = object()
    monkeypatch.setitem(PostgresServer._instances, dev.DATA_DIR.resolve(), stale)
    server, uri = dev.start_postgres()
    assert server is fake and uri.endswith("/postgres") and len(calls) == 2
    assert dev.DATA_DIR.resolve() not in PostgresServer._instances       # la instancia a medias se descartó


def test_start_postgres_gives_a_clear_message_if_it_never_recovers(monkeypatch):
    import dev
    import pgserver

    monkeypatch.setattr(pgserver, "get_server", lambda path, cleanup_mode: (_ for _ in ()).throw(subprocess.TimeoutExpired("pg_ctl", 10)))
    monkeypatch.setattr(dev, "wait_for_recovery", lambda: False)
    with pytest.raises(SystemExit, match="no terminó de recuperarse"):
        dev.start_postgres()


def test_start_postgres_does_not_wait_when_the_server_starts_normally(monkeypatch):
    import dev
    import pgserver

    fake = type("S", (), {"get_uri": lambda self: "postgresql://x"})()
    monkeypatch.setattr(pgserver, "get_server", lambda path, cleanup_mode: fake)
    monkeypatch.setattr(dev, "wait_for_recovery", lambda: pytest.fail("no debía esperar"))
    assert dev.start_postgres() == (fake, "postgresql://x")


# ----------------------------- PostgreSQL desechable de las pruebas: reintenta y no deja instancias a medias
def test_the_test_database_retries_and_drops_the_half_started_instance(monkeypatch, tmp_path):
    import pgserver
    from pgserver.postgres_server import PostgresServer
    from honolulo_common import testing

    calls, data_dir = [], tmp_path / "pg"
    ok = type("S", (), {"get_uri": lambda self: "postgresql://x"})()

    def get_server(path, cleanup_mode):
        calls.append(len(calls) + 1)
        PostgresServer._instances[path.resolve()] = "instancia a medias"        # lo que deja un arranque fallido
        if len(calls) < 3:
            raise subprocess.TimeoutExpired("pg_ctl", 10)
        PostgresServer._instances.pop(path.resolve(), None)
        return ok

    monkeypatch.setattr(pgserver, "get_server", get_server)
    monkeypatch.setattr(testing.time, "sleep", lambda s: None)
    data_dir.mkdir()
    (data_dir / "pg_notify_perdido").write_text("dañado")
    assert testing._start_embedded(data_dir) is ok and len(calls) == 3
    assert data_dir.resolve() not in PostgresServer._instances                  # no quedó nada en la caché de pgserver
    assert not (data_dir / "pg_notify_perdido").exists()                         # tras dos fallos se recreó el directorio


def test_the_test_database_gives_up_with_the_original_error_after_three_attempts(monkeypatch, tmp_path):
    import pgserver
    from honolulo_common import testing

    monkeypatch.setattr(pgserver, "get_server", lambda path, cleanup_mode: (_ for _ in ()).throw(RuntimeError("no arranca")))
    monkeypatch.setattr(testing.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="no arranca"):
        testing._start_embedded(tmp_path / "pg")
