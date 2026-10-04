"""Llamadas a los microservicios con la sesión del usuario (cookies HttpOnly → Bearer).

El navegador nunca ve los tokens en JavaScript. Cuando el access token vence, la pasarela lo
renueva con el refresh token. Como los refresh tokens rotan (y reusar uno revoca la sesión), las
renovaciones se serializan y el resultado se comparte brevemente entre peticiones paralelas.

``SessionState`` lleva la configuración y el request-id para poder usarse desde hilos sin contexto
de Flask (p. ej. el refresco combinado de clima y pronóstico).
"""

from __future__ import annotations

import threading
import time

import requests
from flask import current_app, g, request

SERVICES = {"auth": "AUTH_SERVICE_URL", "weather": "WEATHER_SERVICE_URL", "forecast": "FORECAST_SERVICE_URL",
            "catalog": "CATALOG_SERVICE_URL"}


class UpstreamUnavailable(Exception):
    def __init__(self, service: str) -> None:
        super().__init__(service)
        self.service = service


class SessionState:
    """Tokens de la petición en curso. Es seguro usarlo desde varios hilos."""

    def __init__(self, cfg, request_id: str, access: str | None, refresh: str | None, client_ip: str = "") -> None:
        self.cfg, self.request_id, self.client_ip = cfg, request_id, client_ip
        self.access, self.refresh = access, refresh
        self.rotated: dict | None = None     # tokens nuevos que hay que enviar al navegador
        self.cleared = False                 # la sesión dejó de ser válida
        self.lock = threading.RLock()


class _RefreshCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recent: dict[str, tuple[float, dict]] = {}

    def refresh(self, refresh_token: str, cfg, client_ip: str = "") -> dict | None:
        with self._lock:
            now = time.monotonic()
            self._recent = {k: v for k, v in self._recent.items() if v[0] > now}
            if refresh_token in self._recent:
                return self._recent[refresh_token][1]
            try:
                resp = requests.post(f"{cfg['AUTH_SERVICE_URL'].rstrip('/')}/api/v1/auth/refresh",
                                     json={"refresh_token": refresh_token}, headers=_client_headers(client_ip),
                                     timeout=cfg["UPSTREAM_TIMEOUT_S"])
            except requests.RequestException:
                raise UpstreamUnavailable("auth") from None
            if resp.status_code != 200:
                return None
            tokens = resp.json()
            self._recent[refresh_token] = (now + cfg["REFRESH_REUSE_WINDOW_S"], tokens)
            return tokens


coordinator = _RefreshCoordinator()


def _client_headers(client_ip: str) -> dict:
    """IP de la persona que hizo la petición: los servicios la guardan en la auditoría de seguridad."""
    return {"X-Client-IP": client_ip} if client_ip else {}


def init_state() -> None:
    cfg = current_app.config
    g.state = SessionState(cfg, getattr(g, "request_id", ""), request.cookies.get(cfg["ACCESS_COOKIE"]),
                           request.cookies.get(cfg["REFRESH_COOKIE"]), request.remote_addr or "")


def _refresh_session(state: SessionState, failed_access: str | None) -> bool:
    """Renueva la sesión; devuelve False si ya no es válida (y marca las cookies para borrarse)."""
    with state.lock:
        if state.access and state.access != failed_access:
            return True                       # otra petición paralela ya renovó
        if not state.refresh:
            state.cleared = True
            return False
        tokens = coordinator.refresh(state.refresh, state.cfg, state.client_ip)
        if tokens is None:
            state.access = state.refresh = None
            state.cleared = True
            return False
        state.access, state.refresh, state.rotated = tokens["access_token"], tokens["refresh_token"], tokens
        return True


def _request(cfg, service: str, method: str, path: str, *, params=None, json=None, headers=None,
             data=None, files=None):
    try:
        return requests.request(method, cfg[SERVICES[service]].rstrip("/") + path, params=params, json=json,
                                data=data, files=files, headers=headers, timeout=cfg["UPSTREAM_TIMEOUT_S"])
    except requests.RequestException:
        raise UpstreamUnavailable(service) from None


def _send(state: SessionState, service: str, method: str, path: str, *, params=None, json=None,
          data=None, files=None):
    headers = {"X-Request-ID": state.request_id, **_client_headers(state.client_ip)}
    with state.lock:
        access = state.access
    if access:
        headers["Authorization"] = f"Bearer {access}"
    return _request(state.cfg, service, method, path, params=params, json=json, headers=headers,
                    data=data, files=files), access


def downstream(state: SessionState, service: str, method: str, path: str, *, params=None, json=None,
               data=None, files=None):
    """Llama a un servicio con la sesión del usuario; ante 401 renueva una vez y reintenta."""
    if not state.access and state.refresh:
        _refresh_session(state, None)
    kwargs = dict(params=params, json=json, data=data, files=files)
    resp, used = _send(state, service, method, path, **kwargs)
    if resp.status_code == 401 and state.refresh:
        if _refresh_session(state, used):
            resp, _ = _send(state, service, method, path, **kwargs)     # los archivos van como bytes: reutilizables
    return resp


def anonymous(service: str, method: str, path: str, *, json=None, headers=None, params=None):
    """Llamadas sin sesión (login, registro, lectura pública del catálogo y fotos)."""
    cfg = current_app.config
    merged = {"X-Request-ID": getattr(g, "request_id", ""), **_client_headers(request.remote_addr or ""), **(headers or {})}
    return _request(cfg, service, method, path, params=params, json=json, headers=merged)
