"""Errores de API en formato ``application/problem+json`` (RFC 7807)."""

from __future__ import annotations

import logging
from http import HTTPStatus

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"


class ApiError(Exception):
    """Error controlado que se convierte en una respuesta JSON uniforme."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        errors: list[dict] | None = None,
        extra: dict | None = None,
        headers: dict | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = errors
        self.extra = extra or {}
        self.headers = headers or {}


def problem_response(status: int, code: str, detail: str, *, title: str | None = None,
                     errors: list[dict] | None = None, extra: dict | None = None,
                     headers: dict | None = None):
    body = {
        "type": "about:blank",
        "title": title or HTTPStatus(status).phrase,
        "status": status,
        "code": code,
        "detail": detail,
        "request_id": getattr(g, "request_id", None),
    }
    if errors:
        body["errors"] = errors
    if extra:
        body.update(extra)
    resp = jsonify(body)
    resp.status_code = status
    resp.headers["Content-Type"] = PROBLEM_JSON
    for key, value in (headers or {}).items():
        resp.headers[key] = value
    return resp


def validation_error(messages: dict) -> ApiError:
    """Convierte el resultado de ``marshmallow.ValidationError.messages`` en un ApiError 422."""
    errors = []
    for field, msgs in messages.items():
        if isinstance(msgs, (list, tuple)):
            errors.extend({"field": field, "message": str(m)} for m in msgs)
        else:
            errors.append({"field": field, "message": str(msgs)})
    return ApiError(422, "VALIDATION_ERROR", "Revisa los campos marcados.", errors=errors)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return problem_response(exc.status, exc.code, exc.detail, errors=exc.errors,
                                extra=exc.extra, headers=exc.headers)

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        status = exc.code or 500
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 400: "BAD_REQUEST",
                413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE"}.get(status, "HTTP_ERROR")
        return problem_response(status, code, exc.description or HTTPStatus(status).phrase)

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        log.exception("Error no controlado en %s %s", request.method, request.path)
        return problem_response(500, "INTERNAL_ERROR", "Ocurrió un error inesperado.")
