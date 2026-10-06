#!/usr/bin/env python
"""Pruebas de estrés del sistema LOCAL.

Solo acepta 127.0.0.1 / localhost: sirve para medir TU sistema, no para atacar servidores ajenos.

    python scripts/dev.py up --no-rate-limit     # capacidad real, sin el límite por IP
    python scripts/stress.py baseline            # latencia en calma
    python scripts/stress.py read-flood          # muchas lecturas a la vez (--concurrency, --duration)
    python scripts/stress.py ramp                # sube la concurrencia por escalones hasta encontrar el límite
    python scripts/stress.py login-flood         # intentos de ingreso en masa (cada uno cuesta un hash scrypt)
    python scripts/stress.py slow-connections    # cientos de conexiones lentas abiertas (estilo «slowloris»)
    python scripts/stress.py oversize            # cuerpos y direcciones gigantes
    python scripts/stress.py recovery            # ¿vuelve a la normalidad después?
    python scripts/stress.py all                 # todo lo anterior, menos ramp

Con el límite por IP activo, para que el «usuario normal» venga de otra IP distinta de la del atacante hay que arrancar
con ``TRUSTED_PROXY_HOPS=1`` (así se respeta X-Forwarded-For); si no, todas las peticiones locales cuentan como una sola IP.

Cada escenario mide también a un usuario normal (sondeo cada 0,25 s desde otra IP): lo que importa no es solo si el
sistema aguanta, sino si un abusador perjudica a los demás. Los escenarios se registran con ``@scenario``: añadir uno nuevo
no obliga a tocar los demás (principio abierto/cerrado).
"""

from __future__ import annotations

import argparse
import random
import re
import socket
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

import requests

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_CONCURRENCY, MAX_DURATION, MAX_SOCKETS = 500, 120, 1000
ATTACKER_IP, NORMAL_USER_IP = "198.51.100.50", "203.0.113.77"
FALLBACK_SLUG = "catarata-velo-de-las-ninfas"
PROBE_PATHS = ("/politica-de-cookies", "/api/v1/places")
SERVICES = {"auth": 5001, "weather": 5002, "forecast": 5003, "catalog": 5004}


@dataclass(frozen=True)
class Settings:
    base: str
    host: str
    port: int
    concurrency: int
    duration: float
    sockets: int


@dataclass(frozen=True)
class Scenario:
    name: str
    help: str
    run: Callable[[Settings], None]


SCENARIOS: dict[str, Scenario] = {}


def scenario(name: str, help: str):
    def decorator(func):
        SCENARIOS[name] = Scenario(name, help, func)
        return func
    return decorator


# ------------------------------------------------------------------------------------------------ medición
@dataclass
class Samples:
    items: list[tuple[float, str]] = field(default_factory=list)

    def add(self, latency: float, outcome: str) -> None:
        self.items.append((latency, outcome))

    @property
    def ok(self) -> int:
        return sum(1 for _, o in self.items if o[:1] in "23")

    @property
    def errors(self) -> int:
        return sum(1 for _, o in self.items if not o[:1].isdigit() or o[:1] == "5")


def classify_error(exc: Exception) -> str:
    text = str(exc)
    if "10048" in text or "10055" in text:       # Windows se quedó sin puertos locales: culpa de la prueba, no del servidor
        return "PUERTOS-LOCALES"
    return "ERR:" + type(exc).__name__


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else float("nan")


def describe(label: str, samples: Samples, seconds: float) -> None:
    n = len(samples.items)
    outcomes = Counter(o for _, o in samples.items)
    shown = "  ".join(f"{k}:{v}" for k, v in sorted(outcomes.items(), key=lambda kv: -kv[1])[:6]) or "-"
    lat = [lat * 1000 for lat, _ in samples.items]
    print(f"  {label}: {n} peticiones ({n / seconds:.0f}/s) · resultados → {shown}")
    if lat:
        print(f"      latencia ms → p50 {percentile(lat, .5):.0f} · p95 {percentile(lat, .95):.0f} · "
              f"p99 {percentile(lat, .99):.0f} · máx {max(lat):.0f}")


def verdict(samples: Samples) -> str:
    if not samples.items:
        return "SIN DATOS"
    lat = [lat * 1000 for lat, _ in samples.items]
    ok_rate = samples.ok / len(samples.items)
    if ok_rate >= 0.99 and percentile(lat, .95) < 1000:
        return "✅ el usuario normal NO se ve afectado"
    if ok_rate >= 0.9:
        return "⚠️  el usuario normal nota lentitud o algún fallo"
    return "❌ el usuario normal queda sin servicio"


# ------------------------------------------------------------------------------------------------ generadores
class Probe:
    """Un «usuario normal» que pide páginas cada 0,25 s desde otra IP mientras dura el ataque."""

    def __init__(self, settings: Settings) -> None:
        self.settings, self.samples, self._stop = settings, Samples(), threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        session = requests.Session()
        session.headers["X-Forwarded-For"] = NORMAL_USER_IP
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                outcome = str(session.get(self.settings.base + random.choice(PROBE_PATHS), timeout=15,
                                          allow_redirects=False).status_code)
            except requests.RequestException as exc:
                outcome = classify_error(exc)
            self.samples.add(time.perf_counter() - started, outcome)
            self._stop.wait(0.25)

    def __enter__(self) -> "Probe":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=20)


MakeStep = Callable[[requests.Session], Callable[[], str]]


def run_load(make_step: MakeStep, concurrency: int, duration: float, *, ip: str = ATTACKER_IP) -> Samples:
    """``concurrency`` hilos, cada uno con su propia conexión persistente, repitiendo ``make_step`` durante ``duration`` s."""
    samples, stop = Samples(), threading.Event()
    barrier = threading.Barrier(concurrency + 1)

    def worker() -> None:
        session = requests.Session()
        session.headers["X-Forwarded-For"] = ip
        try:
            step = make_step(session)
        except requests.RequestException as exc:
            samples.add(0.0, classify_error(exc))
            step = None
        try:
            barrier.wait(timeout=60)
        except threading.BrokenBarrierError:
            return
        while step is not None and not stop.is_set():
            started = time.perf_counter()
            try:
                outcome = step()
            except requests.RequestException as exc:
                outcome = classify_error(exc)
            samples.add(time.perf_counter() - started, outcome)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(concurrency)]
    for t in threads:
        t.start()
    barrier.wait(timeout=60)
    time.sleep(duration)
    stop.set()
    for t in threads:
        t.join(timeout=40)
    return samples


def attack(title: str, settings: Settings, make_step: MakeStep, concurrency: int | None = None,
           duration: float | None = None) -> tuple[Samples, Samples]:
    concurrency, duration = concurrency or settings.concurrency, duration or settings.duration
    print(f"\n▶ {title} — {concurrency} conexiones simultáneas durante {duration:.0f} s")
    with Probe(settings) as probe:
        attacker = run_load(make_step, concurrency, duration)
    describe("atacante     ", attacker, duration)
    describe("usuario normal", probe.samples, duration)
    print(f"  → {verdict(probe.samples)}")
    return attacker, probe.samples


def discover_slug(settings: Settings) -> str:
    try:
        data = requests.get(settings.base + "/api/v1/places", timeout=15).json()
        items = data if isinstance(data, list) else data.get("items") or data.get("places") or []
        return next((p["slug"] for p in items if "slug" in p), FALLBACK_SLUG)
    except (requests.RequestException, ValueError, AttributeError):
        return FALLBACK_SLUG


def read_paths(settings: Settings) -> list[str]:
    slug = discover_slug(settings)
    return ["/api/v1/places", f"/api/v1/places/{slug}", f"/api/v1/places/{slug}/reviews", "/politica-de-cookies",
            "/ingresar"]                                  # lo que ve cualquier visitante anónimo


def reads(settings: Settings) -> MakeStep:
    paths = read_paths(settings)

    def make(session: requests.Session) -> Callable[[], str]:
        def step() -> str:
            return str(session.get(settings.base + random.choice(paths), timeout=30, allow_redirects=False).status_code)
        return step
    return make


# ------------------------------------------------------------------------------------------------ escenarios
@scenario("baseline", "latencia de cada página con una sola persona (referencia)")
def baseline(settings: Settings) -> None:
    print("\n▶ baseline — una sola conexión, 30 peticiones por dirección")
    session = requests.Session()
    for path in read_paths(settings):
        samples = Samples()
        for _ in range(30):
            started = time.perf_counter()
            try:
                outcome = str(session.get(settings.base + path, timeout=30, allow_redirects=False).status_code)
            except requests.RequestException as exc:
                outcome = classify_error(exc)
            samples.add(time.perf_counter() - started, outcome)
        lat = [lat * 1000 for lat, _ in samples.items]
        codes = ",".join(sorted(Counter(o for _, o in samples.items)))
        print(f"  {path:<48} http {codes:<6} p50 {percentile(lat, .5):6.1f} ms · p95 {percentile(lat, .95):6.1f} ms")


@scenario("read-flood", "muchas lecturas simultáneas desde una misma IP")
def read_flood(settings: Settings) -> None:
    attack("read-flood", settings, reads(settings))


@scenario("ramp", "sube la concurrencia por escalones para encontrar dónde empieza a degradarse")
def ramp(settings: Settings) -> None:
    step_s = min(settings.duration, 10)
    print(f"\n▶ ramp — escalones de {step_s:.0f} s")
    print(f"  {'conexiones':>10} {'req/s':>7} {'p50 ms':>8} {'p95 ms':>8} {'errores':>8} {'429':>6}   usuario normal")
    make = reads(settings)
    for level in (10, 25, 50, 100, 200, 400):
        if level > settings.concurrency and settings.concurrency != 50:
            break
        with Probe(settings) as probe:
            samples = run_load(make, level, step_s)
        lat = [lat * 1000 for lat, _ in samples.items]
        outcomes = Counter(o for _, o in samples.items)
        plat = [lat_ * 1000 for lat_, _ in probe.samples.items]
        print(f"  {level:>10} {len(lat) / step_s:>7.0f} {percentile(lat, .5):>8.0f} {percentile(lat, .95):>8.0f} "
              f"{samples.errors:>8} {outcomes.get('429', 0):>6}   "
              f"{probe.samples.ok}/{len(probe.samples.items)} ok · p95 {percentile(plat, .95):.0f} ms")
        time.sleep(2)


@scenario("login-flood", "intentos de ingreso en masa (cada uno obliga al servidor a calcular un hash scrypt)")
def login_flood(settings: Settings) -> None:
    def make(session: requests.Session) -> Callable[[], str]:
        page = session.get(settings.base + "/ingresar", timeout=30)
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        token = match.group(1) if match else ""

        def step() -> str:
            data = {"csrf_token": token, "email": f"estres.{random.getrandbits(48):x}@example.com",
                    "password": "Clave-Incorrecta-1"}
            return str(session.post(settings.base + "/ingresar", data=data, timeout=30, allow_redirects=False).status_code)
        return step
    attack("login-flood", settings, make)


@scenario("slow-connections", "cientos de conexiones abiertas que envían la petición a cuentagotas (estilo «slowloris»)")
def slow_connections(settings: Settings) -> None:
    print(f"\n▶ slow-connections — {settings.sockets} conexiones lentas durante {settings.duration:.0f} s")
    held: list[socket.socket] = []
    refused = 0
    for _ in range(settings.sockets):
        try:
            s = socket.create_connection((settings.host, settings.port), timeout=5)
            s.sendall(b"GET /politica-de-cookies HTTP/1.1\r\nHost: localhost\r\nX-Lento: ")
            held.append(s)
        except OSError:
            refused += 1
    print(f"  conexiones abiertas: {len(held)} · rechazadas: {refused}")
    with Probe(settings) as probe:
        deadline = time.time() + settings.duration
        while time.time() < deadline:
            time.sleep(2)
            for s in list(held):                      # un byte más para que parezcan vivas
                try:
                    s.sendall(b"a")
                except OSError:
                    held.remove(s)
    describe("usuario normal", probe.samples, settings.duration)
    print(f"  conexiones lentas que el servidor sigue tolerando al final: {len(held)} de {settings.sockets - refused}")
    print(f"  → {verdict(probe.samples)}")
    for s in held:
        s.close()


@scenario("oversize", "cuerpos y direcciones gigantes: ¿se rechazan rápido y sin colgar nada?")
def oversize(settings: Settings) -> None:
    print("\n▶ oversize")
    session = requests.Session()
    cases = [("cuerpo de 1 MB", lambda: session.post(settings.base + "/ingresar", data=b"a=" + b"x" * 1_000_000,
                                                      headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)),
             ("cuerpo de 7 MB (límite 6 MB)", lambda: session.post(settings.base + "/ingresar", data=b"a=" + b"x" * 7_000_000,
                                                                  headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)),
             ("cuerpo de 40 MB", lambda: session.post(settings.base + "/ingresar", data=b"a=" + b"x" * 40_000_000,
                                                      headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=120)),
             ("dirección de 100 000 caracteres", lambda: session.get(settings.base + "/?q=" + "a" * 100_000, timeout=30,
                                                                    allow_redirects=False)),
             ("500 cabeceras", lambda: session.get(settings.base + "/politica-de-cookies", timeout=30,
                                                   headers={f"X-Basura-{i}": "x" * 50 for i in range(500)}))]
    for label, send in cases:
        started = time.perf_counter()
        try:
            outcome = f"http {send().status_code}"
        except requests.RequestException as exc:
            outcome = classify_error(exc)
        print(f"  {label:<36} → {outcome:<22} en {(time.perf_counter() - started) * 1000:7.0f} ms")
        session = requests.Session()


@scenario("recovery", "¿vuelve todo a la normalidad cuando termina la carga?")
def recovery(settings: Settings) -> None:
    print("\n▶ recovery")
    time.sleep(3)
    healthy = True
    targets = [("web /healthz", settings.base + "/healthz"), ("web /readyz", settings.base + "/readyz")]
    targets += [(f"{name} /healthz", f"http://{settings.host}:{port}/healthz") for name, port in SERVICES.items()]
    for label, url in targets:
        try:
            code = requests.get(url, timeout=10).status_code
        except requests.RequestException as exc:
            code = classify_error(exc)
        healthy &= code == 200
        print(f"  {label:<18} → {code}")
    samples, session = Samples(), requests.Session()
    for _ in range(30):
        started = time.perf_counter()
        try:
            outcome = str(session.get(settings.base + "/api/v1/places", timeout=15).status_code)
        except requests.RequestException as exc:
            outcome = classify_error(exc)
        samples.add(time.perf_counter() - started, outcome)
    describe("30 lecturas seguidas", samples, max(sum(l for l, _ in samples.items), 0.001))
    print("  →", "✅ RECUPERADO: todos los servicios responden" if healthy and samples.ok == 30
          else "❌ algo no se recuperó")


@scenario("all", "baseline, read-flood, login-flood, slow-connections, oversize y recovery")
def run_all(settings: Settings) -> None:
    for name in ("baseline", "read-flood", "login-flood", "slow-connections", "oversize", "recovery"):
        SCENARIOS[name].run(settings)
        time.sleep(3)


# ------------------------------------------------------------------------------------------------ entrada
def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Pruebas de estrés del sistema local (solo 127.0.0.1).")
    parser.add_argument("scenario", choices=sorted(SCENARIOS), metavar="escenario",
                        help="; ".join(f"{s.name}: {s.help}" for s in SCENARIOS.values()))
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=50, help=f"conexiones simultáneas (máx. {MAX_CONCURRENCY})")
    parser.add_argument("--duration", type=float, default=15, help=f"segundos por escenario (máx. {MAX_DURATION})")
    parser.add_argument("--sockets", type=int, default=200, help=f"conexiones lentas (máx. {MAX_SOCKETS})")
    args = parser.parse_args()

    parsed = urlparse(args.base)
    if parsed.hostname not in LOOPBACK:
        raise SystemExit("Solo se permite probar el sistema local (127.0.0.1 / localhost). Esto no es una herramienta "
                         "para atacar servidores ajenos.")
    settings = Settings(base=args.base.rstrip("/"), host=parsed.hostname, port=parsed.port or 80,
                        concurrency=max(1, min(args.concurrency, MAX_CONCURRENCY)),
                        duration=max(1.0, min(args.duration, MAX_DURATION)),
                        sockets=max(1, min(args.sockets, MAX_SOCKETS)))
    SCENARIOS[args.scenario].run(settings)


if __name__ == "__main__":
    main()
