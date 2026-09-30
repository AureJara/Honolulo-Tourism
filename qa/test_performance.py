"""Humo de rendimiento: latencias p50/p95 secuenciales y bajo concurrencia sobre las rutas críticas.
Los umbrales son holgados (equipo de desarrollo, una sola instancia, servidor de desarrollo de Flask)."""

import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import BASE, Web

MONTH = time.strftime("%Y-%m", time.gmtime(time.time() - 5 * 3600))
TODAY = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 5 * 3600))


def p(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def measure(call, n=30, workers=1):
    times = []

    def one(_):
        start = time.perf_counter()
        resp = call()
        times.append((time.perf_counter() - start) * 1000)
        return resp.status_code

    with ThreadPoolExecutor(max_workers=workers) as pool:
        codes = list(pool.map(one, range(n)))
    return times, codes


ENDPOINTS = {
    "clima actual": ("GET", "/api/v1/weather/current", 600),
    "calendario del mes": ("GET", f"/api/v1/weather/calendar?month={MONTH}", 800),
    "pronóstico del día": ("GET", f"/api/v1/weather/forecast?date={TODAY}", 800),
    "historial": ("GET", "/api/v1/weather/history?source=current&page_size=10", 600),
    "lugares (público)": ("GET", "/api/v1/places", 400),
}


@pytest.mark.parametrize("name", list(ENDPOINTS))
def test_sequential_latency(member_web, name):
    method, path, limit_ms = ENDPOINTS[name]
    times, codes = measure(lambda: member_web.api(method, path), n=25)
    p50, p95 = statistics.median(times), p(times, 0.95)
    print(f"\n  {name:<22} p50={p50:6.1f} ms  p95={p95:6.1f} ms  (límite p95 {limit_ms} ms)")
    assert set(codes) == {200}
    assert p95 < limit_ms


@pytest.mark.parametrize("name", list(ENDPOINTS))
def test_latency_under_concurrency_has_no_errors(member_web, name):
    """Las cookies de la sesión se comparten entre hilos: simula 10 pestañas del mismo usuario."""
    method, path, limit_ms = ENDPOINTS[name]
    times, codes = measure(lambda: member_web.api(method, path), n=60, workers=10)
    print(f"\n  {name:<22} ×10 concurrentes p95={p(times, 0.95):6.1f} ms  errores={sum(c >= 500 for c in codes)}")
    assert all(c == 200 for c in codes), set(codes)
    assert p(times, 0.95) < limit_ms * 5


def test_main_page_and_static_assets_load_fast(member_web):
    times, codes = measure(lambda: member_web.get("/"), n=20)
    print(f"\n  página principal       p95={p(times, 0.95):6.1f} ms")
    assert set(codes) == {200} and p(times, 0.95) < 600
    js = requests.get(f"{BASE}/static/js/app.js", timeout=10)
    assert js.status_code == 200 and len(js.content) < 60 * 1024           # el JS propio pesa menos de 60 KB


def test_concurrent_distinct_users_do_not_interfere():
    """Varias sesiones distintas a la vez: cada una ve su propio nombre y nadie recibe datos de otro."""
    from conftest import code_from_mail, latest_mail, unique_email
    users = []
    for i in range(4):
        w, email = Web(), unique_email()
        w.register(email, first=f"Usuaria{chr(65 + i)}bcd", last="Quispe")
        w.verify(email)
        users.append((w, f"Usuaria{chr(65 + i)}bcd"))

    def visit(item):
        w, first = item
        html = w.get("/").text
        return first in html, [other for _, other in users if other != first and other in html]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(visit, users * 5))
    assert all(own for own, _ in results) and not any(leak for _, leak in results)
