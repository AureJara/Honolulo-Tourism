"""Páginas HTML: acceso (RF01), registro con confirmación de correo (Escenarios 7 y 8),
políticas de privacidad y de cookies, y la pantalla principal de Honolulo."""

from __future__ import annotations

import functools
import re

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, session,
                   url_for)
from werkzeug.wrappers import Response

from .security import clear_session_cookies, csrf_token, safe_next, set_session_cookies, verify_csrf_form
from .upstream import UpstreamUnavailable, anonymous, downstream

bp = Blueprint("pages", __name__)

_AUTH_DOWN = "El servicio de autenticación no está disponible. Inténtalo nuevamente en unos minutos."


def current_user() -> dict | None:
    """Usuario de la sesión actual, o None (RF01). Renueva tokens si hace falta."""
    state = g.state
    if not state.access and not state.refresh:
        return None
    try:
        resp = downstream(state, "auth", "GET", "/api/v1/auth/me")
    except UpstreamUnavailable:
        abort(503, description=_AUTH_DOWN)
    return resp.json() if resp.status_code == 200 else None


def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("pages.login", next=request.full_path.rstrip("?")))
        g.user = user
        return view(*args, **kwargs)
    return wrapper


def _body(resp) -> dict:
    try:
        body = resp.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


def _form_errors(resp) -> tuple[dict[str, str], str | None]:
    """Mensajes por campo y uno general a partir de la respuesta de error de un servicio."""
    body = _body(resp)
    fields = {}
    for item in body.get("errors", []):
        if "field" in item:
            fields.setdefault(item["field"], item["message"])
    return fields, (None if fields else body.get("detail") or "No se pudo completar la solicitud.")


def _session_redirect(tokens: dict, target: str) -> Response:
    resp = redirect(target)
    set_session_cookies(resp, tokens)
    return resp


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'*' * max(len(local) - 1, 3)}@{domain}" if domain else email


def _announce_code(body: dict, sent: str, category: str = "ok") -> None:
    """Avisa de que se pidió un código de confirmación. ``auth`` informa en ``delivery`` si el correo sale a Internet: si no
    (desarrollo: el mensaje queda en una carpeta) NO se dice que se envió; se dice dónde quedó y la pantalla del código lo
    recuerda (``mail_local``). Una versión de ``auth`` que no informe nada se trata como envío normal."""
    delivery = body.get("delivery") or {}
    if delivery.get("external", True):
        session.pop("mail_local", None)
        flash(sent, category)
        return
    where = delivery.get("where") or "el sistema de desarrollo"
    session["mail_local"] = where
    flash(f"Modo de desarrollo: el código NO se envió a tu correo, quedó en {where}. "
          "Para recibirlo por correo configura el envío con: python scripts/dev.py setup-mail", "info")


# ----------------------------------------------------------------- acceso
@bp.route("/ingresar", methods=["GET", "POST"])
def login():
    next_url = safe_next(request.values.get("next"))
    if request.method == "GET":
        if current_user():
            return redirect(next_url)
        return render_template("login.html", next_url=next_url, csrf_token=csrf_token(), error=None, email="")

    verify_csrf_form()
    email = request.form.get("email", "").strip()
    try:
        resp = anonymous("auth", "POST", "/api/v1/auth/login",
                         json={"email": email, "password": request.form.get("password", "")})
    except UpstreamUnavailable:
        return render_template("login.html", next_url=next_url, csrf_token=csrf_token(), email=email,
                               error=_AUTH_DOWN), 503
    if resp.status_code == 200:
        return _session_redirect(resp.json(), next_url)
    body = _body(resp)
    if resp.status_code == 403 and body.get("code") == "EMAIL_NOT_VERIFIED":
        session["pending_email"] = body.get("email") or email
        _announce_code(body, "Tu correo aún no está confirmado. Te enviamos un código nuevo.", "info")
        return redirect(url_for("pages.verify"))
    _, message = _form_errors(resp)
    return render_template("login.html", next_url=next_url, csrf_token=csrf_token(), email=email,
                           error=message or "Revisa el correo y la contraseña."), \
        resp.status_code if resp.status_code in (401, 422, 429) else 400


@bp.route("/registro", methods=["GET", "POST"])
def register():
    def render(values, errors, error=None, status=200):
        return render_template("register.html", csrf_token=csrf_token(), values=values, errors=errors,
                               error=error), status

    if request.method == "GET":
        return render({}, {})

    verify_csrf_form()
    values = {k: request.form.get(k, "").strip() for k in ("first_name", "last_name", "email")}
    password, confirm = request.form.get("password", ""), request.form.get("password_confirm", "")
    accepted = request.form.get("accept_privacy") == "on"
    if password != confirm:
        # Solo esta comprobación es local (la confirmación no viaja al servicio). Sin consentimiento o con
        # nombres inválidos SÍ se consulta al servicio: rechaza sin crear nada y devuelve todos los errores juntos.
        return render(values, {"password_confirm": "Las contraseñas no coinciden."}, status=422)

    try:
        resp = anonymous("auth", "POST", "/api/v1/auth/register",
                         json={**values, "password": password, "accept_privacy": accepted})
    except UpstreamUnavailable:
        return render(values, {}, _AUTH_DOWN, 503)
    if resp.status_code == 201:
        session["pending_email"] = _body(resp).get("email", values["email"])
        _announce_code(_body(resp), "Te enviamos un código de confirmación por correo.")
        return redirect(url_for("pages.verify"))
    body = _body(resp)
    if resp.status_code == 503 and body.get("code") == "EMAIL_DELIVERY_FAILED":
        session["pending_email"] = values["email"]          # la cuenta quedó pendiente: puede reenviar
        flash(body.get("detail", "No pudimos enviar el correo."), "error")
        return redirect(url_for("pages.verify"))
    fields, message = _form_errors(resp)
    if resp.status_code == 409:
        fields, message = {"email": message or "Ya existe una cuenta con ese correo."}, None
    return render(values, fields, message, resp.status_code if resp.status_code in (409, 422, 429) else 400)


@bp.route("/verificar", methods=["GET", "POST"])
def verify():
    """Confirmación del correo con el código de 6 dígitos (Escenario 7)."""
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("pages.login"))

    def render(error=None, status=200):
        return render_template("verify.html", csrf_token=csrf_token(), email_masked=mask_email(email),
                               local_mailbox=session.get("mail_local"), error=error), status

    if request.method == "GET":
        return render()

    verify_csrf_form()
    code = re.sub(r"[\s-]", "", request.form.get("code", ""))
    if not re.fullmatch(r"[0-9]{6}", code):
        return render("El código tiene 6 dígitos.", 422)
    try:
        resp = anonymous("auth", "POST", "/api/v1/auth/verify-email", json={"email": email, "code": code})
    except UpstreamUnavailable:
        return render(_AUTH_DOWN, 503)
    if resp.status_code == 200:
        session.pop("pending_email", None)
        session.pop("mail_local", None)
        flash("¡Correo confirmado! Bienvenido a Honolulo.", "ok")
        return _session_redirect(resp.json(), url_for("pages.index"))
    _, message = _form_errors(resp)
    return render(message or "Código incorrecto o vencido.", resp.status_code if resp.status_code in (400, 422) else 400)


@bp.post("/verificar/reenviar")
def resend_code():
    verify_csrf_form()
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("pages.login"))
    try:
        resp = anonymous("auth", "POST", "/api/v1/auth/resend-code", json={"email": email})
        _announce_code(_body(resp), "Si tu cuenta está pendiente de confirmar, te enviamos un código nuevo.")
    except UpstreamUnavailable:
        flash(_AUTH_DOWN, "error")
    return redirect(url_for("pages.verify"))


@bp.post("/salir")
def logout():
    verify_csrf_form()
    state = g.state
    if state.refresh:
        try:
            anonymous("auth", "POST", "/api/v1/auth/logout", json={"refresh_token": state.refresh})
        except UpstreamUnavailable:
            pass                                        # igual se cierra la sesión del navegador
    resp = redirect(url_for("pages.login"))
    clear_session_cookies(resp)
    state.rotated = None
    state.cleared = False                               # ya se limpiaron arriba
    return resp


# ------------------------------------------------------------------ contenido
@bp.get("/politica-de-privacidad")
def privacy():
    return render_template("privacy.html", csrf_token=csrf_token(), user=None)


@bp.get("/politica-de-cookies")
def cookies():
    """Qué cookies usa el sitio (solo esenciales), cuánto duran y para qué sirven."""
    cfg = current_app.config
    days = cfg["REFRESH_COOKIE_MAX_AGE"] // 86400
    table = [
        (cfg["ACCESS_COOKIE"], "Mantiene tu sesión iniciada (credencial de acceso de corta duración).",
         f"{cfg['ACCESS_TOKEN_MINUTES']} minutos"),
        (cfg["REFRESH_COOKIE"], "Renueva tu sesión sin pedirte de nuevo la contraseña.",
         f"hasta {days} días o hasta que cierres sesión"),
        (cfg["CSRF_COOKIE"], "Protege los formularios contra envíos falsos desde otros sitios.",
         "hasta que cierres el navegador"),
        (cfg["SESSION_COOKIE_NAME"], "Recuerda temporalmente el correo pendiente de confirmar y los avisos en pantalla.",
         "hasta que cierres el navegador"),
    ]
    return render_template("cookies.html", csrf_token=csrf_token(), user=None, cookies=table)


@bp.get("/")
@login_required
def index():
    return render_template("index.html", user=g.user, csrf_token=csrf_token())
