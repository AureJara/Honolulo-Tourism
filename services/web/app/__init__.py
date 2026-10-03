from __future__ import annotations

import requests
from flask import Flask, abort, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, MethodNotAllowed, NotFound, RequestEntityTooLarge

from honolulo_common.app_setup import configure_logging, init_request_id, require_secrets
from honolulo_common.errors import ApiError, problem_response, register_error_handlers
from honolulo_common.location import Location

from .config import Config
from .security import apply_session_changes, issue_csrf_cookie
from .upstream import init_state


def _dms(value: float, positive: str, negative: str) -> str:
    hemi = positive if value >= 0 else negative
    value = abs(value)
    deg = int(value)
    minutes = int((value - deg) * 60)
    seconds = round(((value - deg) * 60 - minutes) * 60)
    if seconds == 60:
        minutes, seconds = minutes + 1, 0
    return f"{deg}°{minutes:02d}′{seconds:02d}″{hemi}"


def create_app(overrides: dict | None = None) -> Flask:
    configure_logging()
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    require_secrets(app, "SECRET_KEY")

    init_request_id(app)
    register_error_handlers(app)                 # ApiError → JSON (API)
    app.before_request(init_state)
    app.after_request(apply_session_changes)
    app.after_request(issue_csrf_cookie)

    location = Location.from_env()

    @app.before_request
    def limit_request_size():
        """Solo la subida de fotos del administrador puede ser grande; el resto es diminuto."""
        is_upload = request.path.startswith("/admin/lugares/") and request.path.endswith("/fotos")
        if not is_upload and (request.content_length or 0) > app.config["SMALL_REQUEST_LIMIT"]:
            abort(413)

    @app.context_processor
    def inject_globals():
        return {"policy_version": app.config["PRIVACY_POLICY_VERSION"],
                "contact_email": app.config["CONTACT_EMAIL"], "location": location,
                "coords_dms": f"{_dms(location.latitude, 'N', 'S')} {_dms(location.longitude, 'E', 'O')}"}

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=(), payment=()")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # Sin restringir scripts (el diseño usa Tailwind por CDN), pero sí lo que no depende de ellos:
        # nadie puede enmarcar el sitio, cambiar su <base> ni enviar formularios a otro dominio.
        resp.headers.setdefault("Content-Security-Policy",
                                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
        if app.config["COOKIE_SECURE"]:                      # HTTPS (producción): el navegador no volverá a usar HTTP
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if not request.path.startswith("/static/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        """Páginas HTML para navegación; JSON para /api."""
        status = exc.code or 500
        message = exc.description
        if isinstance(exc, NotFound):
            message = "La página que buscas no existe."
        elif isinstance(exc, MethodNotAllowed):
            message = "Método no permitido."
        elif isinstance(exc, RequestEntityTooLarge):
            message = "El archivo o la solicitud es demasiado grande."
        if request.path.startswith("/api/"):
            return problem_response(status, "HTTP_ERROR", message or exc.name)
        return render_template("error.html", status=status, title=exc.name, message=message), status

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        app.logger.exception("Error no controlado en %s %s", request.method, request.path)
        if request.path.startswith("/api/"):
            return problem_response(500, "INTERNAL_ERROR", "Ocurrió un error inesperado.")
        return render_template("error.html", status=500, title="Error inesperado",
                               message="Ocurrió un error inesperado. Inténtalo nuevamente."), 500

    from .admin import bp as admin_bp
    from .admin_reviews import bp as admin_reviews_bp
    from .api import bp as api_bp
    from .pages import bp as pages_bp
    from .reviews_api import bp as reviews_api_bp
    for blueprint in (api_bp, reviews_api_bp, pages_bp, admin_bp, admin_reviews_bp):
        app.register_blueprint(blueprint)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service="web")

    @app.get("/readyz")
    def readyz():
        """Informativo: estado de cada microservicio (la web responde aunque alguno falle)."""
        deps = {}
        for name, key in (("auth", "AUTH_SERVICE_URL"), ("weather", "WEATHER_SERVICE_URL"),
                          ("forecast", "FORECAST_SERVICE_URL"), ("catalog", "CATALOG_SERVICE_URL")):
            try:
                ok = requests.get(app.config[key].rstrip("/") + "/healthz", timeout=2).status_code == 200
            except requests.RequestException:
                ok = False
            deps[name] = "ok" if ok else "down"
        return jsonify(status="ready", service="web", dependencies=deps)

    return app
