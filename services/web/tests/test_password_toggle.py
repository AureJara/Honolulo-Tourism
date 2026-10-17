"""El «ojito» para mostrar u ocultar la contraseña: que esté en cada campo de contraseña (y solo en ellos), que sea
accesible, que la contraseña nunca llegue escrita en el HTML y que el script haga lo que dice (se ejecuta con Node
contra un DOM mínimo; si no hay Node, esa parte se omite)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import csrf_from, set_pending_email

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
TOGGLE = re.compile(r"<button[^>]*data-password-toggle=\"([^\"]+)\"[^>]*>")


def toggles(html: str) -> list[tuple[str, str]]:
    """(id del campo, etiqueta del botón) de cada ojito de la página."""
    return [(m.group(1), m.group(0)) for m in TOGGLE.finditer(html)]


def input_tags(html: str) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in re.finditer(r"<input[^>]*\bid=\"([^\"]+)\"[^>]*>", html)}


# ----------------------------------------------------------------------------------------- las páginas
def test_login_has_one_eye_for_its_password_field(client):
    html = client.get("/ingresar").get_data(as_text=True)
    [(target, button)] = toggles(html)
    assert target == "password" and 'type="password"' in input_tags(html)["password"]
    assert 'aria-controls="password"' in button and 'aria-pressed="false"' in button
    assert 'aria-label="Mostrar contraseña"' in button
    assert 'type="button"' in button                                  # nunca envía el formulario


def test_register_has_an_eye_for_each_password_field_and_for_nothing_else(client):
    html = client.get("/registro").get_data(as_text=True)
    assert [target for target, _ in toggles(html)] == ["password", "password_confirm"]
    inputs = input_tags(html)
    for target, button in toggles(html):
        assert target in inputs and 'type="password"' in inputs[target]       # cada ojito gobierna un campo que existe
        assert f'aria-controls="{target}"' in button and 'type="button"' in button
    for plain in ("first_name", "last_name", "email"):
        assert 'type="password"' not in inputs[plain] and plain not in [t for t, _ in toggles(html)]


def test_the_icon_is_decorative_and_the_text_is_not_covered_by_the_button(client):
    html = client.get("/registro").get_data(as_text=True)
    assert html.count("data-password-icon") == 2 and 'aria-hidden="true" data-password-icon' in html
    for target in ("password", "password_confirm"):
        assert "pr-12" in input_tags(html)[target]                    # el texto no queda debajo del botón
    assert "pr-12" not in input_tags(html)["email"]


def test_the_password_is_never_written_into_the_page_even_after_an_error(client, mocked):
    secret = "Clave-Secreta-2026-xyz"
    data = {"first_name": "Ana", "last_name": "Ríos", "email": "ana@gmail.com", "password": secret,
            "password_confirm": "otra-distinta-1", "accept_privacy": "on", "csrf_token": csrf_from(client, "/registro")}
    resp = client.post("/registro", data=data)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 422 and "Las contraseñas no coinciden." in html
    assert secret not in html and "otra-distinta-1" not in html
    assert len(toggles(html)) == 2 and 'type="password"' in input_tags(html)["password"]     # sigue oculta


@pytest.mark.parametrize("path", ["/ingresar", "/registro"])
def test_the_pages_with_password_fields_load_the_script_exactly_once(client, path):
    html = client.get(path).get_data(as_text=True)
    assert html.count("js/password-toggle.js") == 1          # dos copias alternarían dos veces y no se vería nada
    assert client.get("/static/js/password-toggle.js").status_code == 200


@pytest.mark.parametrize("path", ["/", "/verificar", "/politica-de-privacidad", "/politica-de-cookies"])
def test_the_pages_without_password_fields_do_not_carry_the_script_or_the_word(client, path):
    """Las demás pantallas no cargan código que no usan; además la QA exige que, por ejemplo, la de auditoría no
    contenga la palabra «password» (se vigila que ninguna clave se cuele en una pantalla)."""
    set_pending_email(client)                       # /verificar solo se muestra con un correo pendiente de confirmar
    html = client.get(path).get_data(as_text=True)
    assert "password-toggle" not in html and "password" not in html.lower().replace("password_", "")


def test_every_template_with_a_password_field_loads_the_script():
    """Si alguien añade un campo de contraseña en otra plantilla y olvida el script, esta prueba se lo recuerda."""
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    with_fields = {p.name for p in templates.rglob("*.html") if p.name != "_password_toggle.html"
                   and re.search(r'type\s*=\s*"password"|password_toggle\(', p.read_text(encoding="utf-8"))}
    assert {"login.html", "register.html"} <= with_fields                  # que la búsqueda no pase en vacío
    for name in with_fields:
        source = next(templates.rglob(name)).read_text(encoding="utf-8")
        assert "js/password-toggle.js" in source, f"{name} tiene un campo de contraseña pero no carga el script del ojito"


def test_edge_does_not_show_its_own_eye_next_to_ours():
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert "::-ms-reveal" in css and "display: none" in css.split("::-ms-reveal")[1][:120]


# ------------------------------------------------------------------------------------ el script, de verdad
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const handlers = { document: {}, window: {} };
const makeInput = (id) => ({ id, type: 'password', selectionStart: 2, selectionEnd: 4,
  setSelectionRange(s, e) { this.selectionStart = s; this.selectionEnd = e; this.restored = [s, e]; } });
const makeButton = (target) => { const icon = { textContent: 'visibility' };
  return { icon, attrs: { 'data-password-toggle': target, 'aria-pressed': 'false' },
    getAttribute(n) { return this.attrs[n]; }, setAttribute(n, v) { this.attrs[n] = v; },
    querySelector(sel) { return sel === '[data-password-icon]' ? icon : null; },
    closest(sel) { return sel === '[data-password-toggle]' ? this : null; } }; };
const inputs = { password: makeInput('password'), password_confirm: makeInput('password_confirm') };
const buttons = [makeButton('password'), makeButton('password_confirm'), makeButton('no_existe')];
const document = {
  getElementById: (id) => inputs[id] || null,
  addEventListener: (type, fn) => { handlers.document[type] = fn; },
  querySelectorAll: (sel) => buttons.filter((b) => !sel.includes('aria-pressed="true"') || b.attrs['aria-pressed'] === 'true'),
};
const window = { addEventListener: (type, fn) => { handlers.window[type] = fn; } };
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), { document, window });

const click = (target) => handlers.document.click({ target });
const state = () => ({ password: inputs.password.type, confirm: inputs.password_confirm.type,
  pressed: buttons.map((b) => b.attrs['aria-pressed']), icons: buttons.map((b) => b.icon.textContent) });
const out = {};
click(buttons[0]);                       out.shown = state(); out.restored = inputs.password.restored;
click(buttons[0]);                       out.hidden_again = state();
click(buttons[0]); click(buttons[1]);    out.both_shown = state();
handlers.window.pagehide();              out.after_pagehide = state();
click({ closest: () => null });          out.ignored_click = state();
click(buttons[2]);                       out.missing_field_ok = true;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def behaviour(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("hace falta Node para ejecutar el script")
    harness = tmp_path_factory.mktemp("js") / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    run = subprocess.run([node, str(harness), str(STATIC / "js" / "password-toggle.js")],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_clicking_the_eye_shows_only_that_password_and_swaps_the_icon(behaviour):
    shown = behaviour["shown"]
    assert shown["password"] == "text" and shown["confirm"] == "password"
    assert shown["pressed"][:2] == ["true", "false"] and shown["icons"][:2] == ["visibility_off", "visibility"]


def test_clicking_again_hides_it_and_the_cursor_position_is_kept(behaviour):
    again = behaviour["hidden_again"]
    assert again["password"] == "password" and again["pressed"][0] == "false" and again["icons"][0] == "visibility"
    assert behaviour["restored"] == [2, 4]


def test_leaving_the_page_hides_every_password(behaviour):
    assert behaviour["both_shown"]["password"] == behaviour["both_shown"]["confirm"] == "text"
    after = behaviour["after_pagehide"]
    assert after["password"] == after["confirm"] == "password" and set(after["pressed"]) == {"false"}


def test_clicks_elsewhere_and_buttons_without_a_field_do_nothing_and_never_fail(behaviour):
    assert behaviour["ignored_click"] == behaviour["after_pagehide"] and behaviour["missing_field_ok"] is True
