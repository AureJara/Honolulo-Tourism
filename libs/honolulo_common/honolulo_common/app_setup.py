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


def require_production_secrets(app: Flask, *keys: str) -> None:
    """En producción no se aceptan secretos por defecto."""
    if app.config.get("APP_ENV") != "production":
        return
    for key in keys:
        value = app.config.get(key, "")
        if not value or str(value).startswith("dev-"):
            raise RuntimeError(f"{key} debe configurarse con un valor seguro en producción")
