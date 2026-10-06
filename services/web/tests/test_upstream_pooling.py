"""Las llamadas internas de la web reutilizan conexiones y fallan rápido si un servicio está caído."""

import time

import pytest

from app import upstream
from app.config import Config
from honolulo_common.testing import CountingHttpServer


def cfg(url: str, connect: float = 1.0) -> dict:
    return {"CATALOG_SERVICE_URL": url, "UPSTREAM_TIMEOUT_S": 5.0, "UPSTREAM_CONNECT_TIMEOUT_S": connect}


def test_many_internal_calls_share_one_connection():
    """Antes cada llamada abría una conexión nueva y dejaba un puerto local ocupado por minutos."""
    with CountingHttpServer() as server:
        for _ in range(25):
            assert upstream._request(cfg(server.url), "catalog", "GET", "/api/v1/places").status_code == 200
        assert (server.connections, server.requests) == (1, 25)


def test_internal_calls_never_carry_cookies_between_people():
    with CountingHttpServer(set_cookie="hn_access=de-otra-persona; Path=/") as server:
        for _ in range(3):
            upstream._request(cfg(server.url), "catalog", "GET", "/x", headers={"Authorization": "Bearer a"})
        assert server.cookies_seen == [None, None, None]


def test_a_service_that_is_down_is_reported_in_seconds_not_after_the_full_wait():
    started = time.perf_counter()
    with pytest.raises(upstream.UpstreamUnavailable) as caught:
        upstream._request(cfg("http://127.0.0.1:9"), "catalog", "GET", "/api/v1/places")     # puerto cerrado
    assert caught.value.service == "catalog" and time.perf_counter() - started < 3


def test_the_timeout_is_split_between_connecting_and_waiting_for_the_answer():
    assert upstream._timeout({"UPSTREAM_CONNECT_TIMEOUT_S": 3.0, "UPSTREAM_TIMEOUT_S": 12.0}) == (3.0, 12.0)
    assert Config.UPSTREAM_CONNECT_TIMEOUT_S < Config.UPSTREAM_TIMEOUT_S
