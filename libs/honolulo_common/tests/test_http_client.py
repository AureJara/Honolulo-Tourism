"""Cliente HTTP entre servicios: reutiliza conexiones, no comparte cookies y repite solo lo que es seguro repetir."""

import threading
import time

import pytest
import requests

from honolulo_common.http_client import service_session
from honolulo_common.testing import CountingHttpServer


def test_plain_requests_opens_one_connection_per_call():
    """Documenta el problema que se corrige: cada ``requests.get`` abre (y gasta) una conexión nueva."""
    with CountingHttpServer() as server:
        for _ in range(10):
            assert requests.get(server.url, timeout=5).status_code == 200
        assert server.connections == 10


def test_a_service_session_reuses_a_single_connection():
    with CountingHttpServer() as server:
        session = service_session()
        for _ in range(30):
            assert session.get(server.url + "/x", timeout=5).status_code == 200
        assert (server.connections, server.requests) == (1, 30)


def test_calls_from_many_threads_share_the_pool():
    with CountingHttpServer() as server:
        session = service_session()
        failures = []

        def worker():
            for _ in range(15):
                try:
                    session.get(server.url, timeout=10).raise_for_status()
                except requests.RequestException as exc:       # pragma: no cover - solo si falla
                    failures.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not failures and server.requests == 8 * 15
        assert server.connections <= 8                         # una por hilo como máximo, no una por llamada


def test_cookies_are_never_stored_or_replayed():
    """Una sesión compartida con cookies las mezclaría entre personas distintas."""
    with CountingHttpServer(set_cookie="sid=de-otra-persona; Path=/") as server:
        session = service_session()
        session.get(server.url, timeout=5)
        session.get(server.url, timeout=5)
        assert server.cookies_seen == [None, None]
        assert len(session.cookies) == 0


def test_a_read_is_repeated_once_when_the_server_closed_the_reused_connection():
    with CountingHttpServer(drop_second_request=True) as server:
        session = service_session()
        assert session.get(server.url, timeout=5).status_code == 200      # conexión 1
        assert session.get(server.url, timeout=5).status_code == 200      # la 1 se cae → se repite en la 2
        assert server.connections == 2 and server.requests == 3


def test_a_write_is_never_repeated_it_could_run_twice():
    with CountingHttpServer(drop_second_request=True) as server:
        session = service_session()
        assert session.get(server.url, timeout=5).status_code == 200
        with pytest.raises(requests.exceptions.ConnectionError):
            session.post(server.url, json={"a": 1}, timeout=5)
        assert server.requests == 2 and server.connections == 1           # se recibió una sola vez


def test_an_unreachable_service_fails_fast_instead_of_holding_the_thread():
    session = service_session()
    started = time.perf_counter()
    with pytest.raises(requests.exceptions.ConnectionError):
        session.get("http://127.0.0.1:9/", timeout=(1, 5))                # puerto cerrado
    assert time.perf_counter() - started < 3


def test_the_pool_size_is_configurable():
    adapter = service_session(max_connections=7).get_adapter("http://servicio")
    assert adapter._pool_maxsize == 7
