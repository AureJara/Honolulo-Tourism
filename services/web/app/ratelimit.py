"""Límite de peticiones por IP en la pasarela web.

Cada ``Rule`` dice a qué rutas aplica, cuántas peticiones admite una IP y en cuánto tiempo. ``RateLimiter`` las evalúa y
guarda los contadores en un ``RateLimitStore``. Principio abierto/cerrado: una regla nueva es otro ``Rule`` (``limiter.add``)
y otro almacén (p. ej. Redis, si hubiera varios procesos) es otra clase con el método ``hit``; el resto no cambia.

El almacén por defecto vive en la memoria del proceso: la web corre con un solo worker (ver README). Con varios
procesos o servidores habría que usar un almacén compartido o limitar también en el proxy.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

_ALL_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class RateLimitStore(Protocol):
    def hit(self, key: str, limit: int, window_s: float) -> float:
        """Registra un intento. Devuelve 0 si se permite o los segundos que faltan para poder reintentar."""


class MemoryStore:
    """Ventana deslizante en memoria: guarda la hora de cada intento reciente por clave."""

    PRUNE_EVERY = 500                                   # cada tantas peticiones se descartan las claves ya vencidas

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[deque, float]] = {}
        self._calls = 0

    def hit(self, key: str, limit: int, window_s: float) -> float:
        now = self._clock()
        with self._lock:
            times, _ = self._hits.setdefault(key, (deque(), window_s))
            while times and times[0] <= now - window_s:
                times.popleft()
            self._calls += 1
            if self._calls % self.PRUNE_EVERY == 0:
                self._prune(now)
            if len(times) >= limit:
                return max(times[0] + window_s - now, 0.001)
            times.append(now)
            self._hits[key] = (times, window_s)
            return 0.0

    def _prune(self, now: float) -> None:
        stale = [k for k, (times, window) in self._hits.items() if not times or times[-1] <= now - window]
        for key in stale:
            del self._hits[key]

    def __len__(self) -> int:
        return len(self._hits)


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_s: int
    pattern: re.Pattern
    message: str
    methods: frozenset[str] = _ALL_METHODS

    def matches(self, method: str, path: str) -> bool:
        return method in self.methods and self.pattern.search(path) is not None


@dataclass(frozen=True)
class Violation:
    rule: Rule
    retry_after: float


class RateLimiter:
    def __init__(self, rules: Iterable[Rule], store: RateLimitStore, enabled: bool = True) -> None:
        self.rules = list(rules)
        self.store = store
        self.enabled = enabled

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)

    def check(self, method: str, path: str, ip: str) -> Violation | None:
        """Cuenta la petición contra cada regla que le aplica; devuelve la infracción con más espera, si la hay."""
        if not self.enabled:
            return None
        worst: Violation | None = None
        for rule in self.rules:
            if not rule.matches(method, path):
                continue
            wait = self.store.hit(f"{rule.name}|{ip}", rule.limit, rule.window_s)
            if wait and (worst is None or wait > worst.retry_after):
                worst = Violation(rule, wait)
        return worst


def _rule(name: str, limit: int, window_s: int, path: str, message: str, methods=_ALL_METHODS) -> Rule:
    return Rule(name, limit, window_s, re.compile(path), message, frozenset(methods))


def default_rules() -> list[Rule]:
    """Límites por IP. Pensados para frenar abusos (adivinar contraseñas o códigos, correos masivos, spam) sin molestar
    a una persona real; varias personas detrás de una misma red (un aula, un hotel) caben holgadamente."""
    wait = "Espera unos minutos e inténtalo de nuevo."
    return [
        _rule("login", 10, 300, r"^/ingresar$", f"Demasiados intentos de ingreso desde tu conexión. {wait}", ["POST"]),
        _rule("register", 10, 1800, r"^/registro$", f"Se crearon demasiadas cuentas desde tu conexión. {wait}", ["POST"]),
        _rule("verify", 15, 600, r"^/verificar$", f"Demasiados intentos con el código. {wait}", ["POST"]),
        _rule("resend", 5, 600, r"^/verificar/reenviar$", f"Pediste demasiados códigos. {wait}", ["POST"]),
        _rule("review-write", 20, 600, r"^/api/v1/places/[^/]+/reviews/mine$", f"Demasiados cambios en tus opiniones. {wait}",
              ["PUT", "DELETE"]),
        _rule("moderation", 60, 300, r"^(/api/v1/reviews/|/admin/opiniones/)", "Demasiadas acciones de moderación seguidas.",
              _WRITE_METHODS),
        _rule("weather-refresh", 20, 300, r"^/api/v1/weather/(forecast/)?refresh$",
              "Demasiadas actualizaciones del clima seguidas.", ["POST"]),
        _rule("photo-upload", 30, 600, r"^/admin/lugares/[^/]+/fotos$", "Demasiadas subidas de fotos seguidas.", ["POST"]),
        # Cualquier página o llamada a la API (los archivos estáticos y las fotos se cachean y quedan fuera).
        _rule("global", 300, 60, r"^/(?!static/|media/)", f"Demasiadas solicitudes desde tu conexión. {wait}"),
    ]
