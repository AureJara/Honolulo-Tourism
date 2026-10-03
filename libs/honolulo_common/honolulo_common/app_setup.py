"""Piezas comunes de la *application factory*: logging, request-id y health checks."""

from __future__ import annotations

import logging
import os
import uuid

from flask import Flask, g, jsonify, request


def configure_logging(level: str | None = None) -> None:
    logging.basicConfig(
        level=(level or os.getenv("LOG_LEVEL", "INFO")).upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # Werkzeug ya registra cada petición; bajamos el ruido de librerías.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def init_request_id(app: Flask) -> None:
    @app.before_request
    def _assign_request_id():
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex

    @app.after_request
    def _echo_request_id(resp):
        resp.headers.setdefault("X-Request-ID", getattr(g, "request_id", ""))
        return resp


def register_health(app: Flask, service: str, db=None) -> None:
    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service=service)

    @app.get("/readyz")
    def readyz():
        if db is not None:
            try:
                db.session.execute(db.text("SELECT 1"))
            except Exception:  # noqa: BLE001
                app.logger.exception("readyz: base de datos no disponible")
                return jsonify(status="unavailable", service=service), 503
        return jsonify(status="ready", service=service)


MIN_SECRET_LENGTH = 32
_WEAK_SECRETS = {"secret", "changeme", "change-me", "password", "honolulo", "admin", "jwt-secret", "dev-secret"}


def require_secrets(app: Flask, *keys: str) -> None:
    """Los secretos nunca viajan en el código: se leen del entorno y el servicio no arranca sin ellos.

    En desarrollo ``scripts/dev.py`` genera valores aleatorios en ``.env.local`` (fuera de git). Las pruebas
    automáticas (``TESTING``) pasan sus propios valores y solo se exige que no estén vacíos.
    """
    testing = bool(app.config.get("TESTING"))
    for key in keys:
        value = str(app.config.get(key) or "")
        if not value:
            raise RuntimeError(
                f"{key} no está configurado. Defínelo en el entorno (en desarrollo, `python scripts/dev.py up` "
                "lo genera solo) con: python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
        if testing:
            continue
        lowered = value.lower()
        if len(value) < MIN_SECRET_LENGTH or lowered in _WEAK_SECRETS or lowered.startswith("dev-"):
            raise RuntimeError(f"{key} es demasiado débil: usa al menos {MIN_SECRET_LENGTH} caracteres aleatorios")

