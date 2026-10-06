"""``dev.py setup-mail``: solo guarda una contraseña de aplicación de Google (16 letras), nunca la contraseña normal.

Si se guardara la contraseña normal, Gmail rechazaría cada envío (error 535) y además quedaría escrita en un archivo."""

import getpass
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import dev  # noqa: E402

GOOD = "abcdefghijklmnop"


@pytest.mark.parametrize("password", [GOOD, "ABCDEFGHIJKLMNOP", "AbCdEfGhIjKlMnOp"])
def test_a_google_app_password_is_16_letters(password):
    assert dev.app_password_problem(password) is None


@pytest.mark.parametrize("password", ["", "corta", "MiClaveNormal1!", "abcdefghijklmno", "abcdefghijklmnopq",
                                      "abcdefghijklmno1", "abcd-efgh-ijkl-mnop", "abcdefghijklmnó1"])
def test_anything_else_is_refused_and_the_message_only_counts_characters(password):
    problem = dev.app_password_problem(password)
    assert problem and "16 letras" in problem and "myaccount.google.com/apppasswords" in problem
    assert f"tiene {len(password)} caracteres" in problem
    if password:
        assert password not in problem                                             # nunca repite lo escrito


def run_setup(monkeypatch, typed_password, saved):
    monkeypatch.setattr("builtins.input", lambda prompt="": "persona@gmail.com")
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": typed_password)
    monkeypatch.setattr(dev.devenv, "update_env_file", lambda values: saved.update(values))
    dev.setup_mail()


def test_setup_mail_saves_a_valid_app_password_and_strips_the_spaces_google_shows(monkeypatch, capsys):
    saved = {}
    run_setup(monkeypatch, "abcd efgh ijkl mnop", saved)
    assert saved == {"SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587", "SMTP_USER": "persona@gmail.com",
                     "SMTP_PASSWORD": GOOD, "SMTP_SECURITY": "starttls"}
    assert GOOD not in capsys.readouterr().out                                    # no se imprime


def test_setup_mail_refuses_a_normal_password_and_saves_nothing(monkeypatch, capsys):
    saved = {}
    with pytest.raises(SystemExit) as stopped:
        run_setup(monkeypatch, "MiClaveNormal1!", saved)
    assert saved == {}                                                             # no se escribió nada en .env.local
    message = str(stopped.value)
    assert "No se guardó nada" in message and "NO uses tu contraseña normal" in message
    assert "MiClaveNormal1!" not in message and "MiClaveNormal1!" not in capsys.readouterr().out


def test_setup_mail_tells_what_to_type_before_asking():
    import inspect
    source = inspect.getsource(dev.setup_mail)
    assert "16 letras" in source and "NO es tu contraseña normal" in source


# ------------------------------------------------- aviso al arrancar con una configuración que Gmail rechazará
def smtp_env(**overrides):
    return {"MAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "persona@gmail.com",
            "SMTP_PASSWORD": GOOD, **overrides}


def test_the_startup_banner_is_quiet_when_the_saved_password_has_the_right_shape():
    banner = dev.mail_banner(smtp_env())
    assert "ENVÍO REAL" in banner and "ATENCIÓN" not in banner


def test_the_startup_banner_warns_when_the_saved_password_cannot_be_an_app_password():
    banner = dev.mail_banner(smtp_env(SMTP_PASSWORD="MiClaveNormal1!"))
    assert "ATENCIÓN" in banner and "tiene 15 caracteres" in banner and "setup-mail" in banner
    assert "MiClaveNormal1!" not in banner


def test_the_startup_banner_does_not_judge_other_mail_providers():
    """SES u otros proveedores usan contraseñas de otra forma: solo se revisa la de Gmail."""
    banner = dev.mail_banner(smtp_env(SMTP_HOST="email-smtp.us-east-1.amazonaws.com", SMTP_PASSWORD="Xk39+largo/clave=SES"))
    assert "ATENCIÓN" not in banner


def test_file_mode_banner_still_says_nothing_is_sent():
    assert "MODO ARCHIVO" in dev.mail_banner({"MAIL_BACKEND": "file"})
