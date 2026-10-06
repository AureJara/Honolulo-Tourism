"""Comandos de desarrollo: usan el Python del proyecto y ``test-mail`` funciona sin base de datos.

Dos fallos reales de la primera prueba de correo: ``python scripts/dev.py up`` con el Python del sistema (sin Pillow ni el
resto de librerías) y ``test-mail`` con ``KeyError: '_SQLA_BASE'`` (nunca había podido funcionar)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import dev  # noqa: E402
import devenv  # noqa: E402


# ------------------------------------------------------------------------------ test-mail
def test_service_env_without_postgres_does_not_need_the_database_url():
    assert "DATABASE_URL" not in dev.service_env({"MAIL_BACKEND": "smtp"}, "auth")


def test_service_env_with_postgres_points_each_service_to_its_own_database():
    env = {"_SQLA_BASE": "postgresql+psycopg://postgres@127.0.0.1:5555"}
    assert dev.service_env(env, "auth")["DATABASE_URL"].endswith("/auth_db")
    assert dev.service_env(env, "catalog")["DATABASE_URL"].endswith("/catalog_db")
    assert "DATABASE_URL" not in dev.service_env(env, "web")


def test_test_mail_runs_without_postgres(monkeypatch):
    """Antes fallaba con ``KeyError: '_SQLA_BASE'`` porque el comando no levanta la base de datos."""
    calls = []
    monkeypatch.setattr(dev, "local_env", lambda mail_mode="auto": {"MAIL_BACKEND": "smtp", "SMTP_HOST": "h"})
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append((args, kw)))
    dev.test_mail("persona@gmail.com")
    [(args, kw)] = calls
    assert args[-2:] == ["mail-test", "persona@gmail.com"] and kw["check"] is True
    assert kw["env"]["APP_ENV"] == "development" and kw["env"]["PYTHONUTF8"] == "1" and "DATABASE_URL" not in kw["env"]


def test_a_failed_test_mail_exits_with_an_error_code_and_no_python_traceback(monkeypatch):
    def failing(args, **kw):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(dev, "local_env", lambda mail_mode="auto": {"MAIL_BACKEND": "smtp"})
    monkeypatch.setattr(subprocess, "run", failing)
    with pytest.raises(SystemExit) as stopped:
        dev.test_mail("persona@gmail.com")
    assert stopped.value.code == 1 and stopped.value.__cause__ is None and stopped.value.__suppress_context__


def test_test_mail_asks_to_configure_the_mail_first(monkeypatch):
    monkeypatch.setattr(dev, "local_env", lambda mail_mode="auto": {"MAIL_BACKEND": "file"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no debe ejecutar nada"))
    with pytest.raises(SystemExit, match="setup-mail"):
        dev.test_mail("persona@gmail.com")


# ----------------------------------------------------------------------- el Python del proyecto
def make_venv(root: Path) -> Path:
    folder, name = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    python = root / ".venv" / folder / name
    python.parent.mkdir(parents=True)
    python.write_text("")
    return python


class FakeChild:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def wait(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture()
def outside_a_venv(monkeypatch):
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)                     # como con el Python del sistema
    monkeypatch.delenv("HONOLULO_SIN_VENV", raising=False)


def test_the_project_python_is_found_in_the_venv_folder(tmp_path):
    assert devenv.project_python(tmp_path) is None
    python = make_venv(tmp_path)
    assert devenv.project_python(tmp_path) == python


def test_with_the_system_python_the_script_is_relaunched_with_the_project_python(monkeypatch, tmp_path, outside_a_venv, capsys):
    python = make_venv(tmp_path)
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: launched.append(cmd) or FakeChild([3]))
    with pytest.raises(SystemExit) as done:
        devenv.run_in_project_venv(["scripts/dev.py", "up", "--mail", "file"], root=tmp_path)
    assert done.value.code == 3                                              # propaga el código de salida del hijo
    assert launched == [[str(python), str(Path("scripts/dev.py").resolve()), "up", "--mail", "file"]]
    assert "entorno virtual del proyecto" in capsys.readouterr().out


def test_ctrl_c_does_not_stop_the_wait_so_the_child_can_shut_everything_down_cleanly(monkeypatch, tmp_path, outside_a_venv):
    """El hijo recibe el mismo Ctrl+C y apaga PostgreSQL y los servicios; el padre no debe irse antes."""
    make_venv(tmp_path)
    child = FakeChild([KeyboardInterrupt(), KeyboardInterrupt(), 0])
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: child)
    with pytest.raises(SystemExit) as done:
        devenv.run_in_project_venv(["scripts/dev.py"], root=tmp_path)
    assert done.value.code == 0 and child.outcomes == []


def test_inside_a_venv_nothing_is_relaunched(monkeypatch, tmp_path):
    make_venv(tmp_path)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix + "-otro")             # ya hay un entorno virtual activo
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: pytest.fail("no debe relanzar"))
    assert devenv.run_in_project_venv(["scripts/dev.py"], root=tmp_path) is None


def test_without_a_project_venv_the_script_just_continues(monkeypatch, tmp_path, outside_a_venv):
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: pytest.fail("no debe relanzar"))
    assert devenv.run_in_project_venv(["scripts/dev.py"], root=tmp_path) is None


def test_the_relaunch_can_be_turned_off(monkeypatch, tmp_path, outside_a_venv):
    make_venv(tmp_path)
    monkeypatch.setenv("HONOLULO_SIN_VENV", "1")
    monkeypatch.setattr(subprocess, "Popen", lambda cmd: pytest.fail("no debe relanzar"))
    assert devenv.run_in_project_venv(["scripts/dev.py"], root=tmp_path) is None


def test_the_real_scripts_ask_for_the_project_python_before_doing_anything():
    """Los tres scripts que se ejecutan a mano deben relanzarse con el Python del proyecto."""
    for name in ("dev.py", "run_tests.py", "stress.py"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "run_in_project_venv(sys.argv)" in source, name
