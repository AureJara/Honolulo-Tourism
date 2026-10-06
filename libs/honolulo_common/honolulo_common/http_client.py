"""Cliente HTTP para las llamadas entre servicios: reutiliza conexiones y no comparte cookies.

``requests.get(...)`` crea una sesión nueva en cada llamada y, con ella, una conexión TCP nueva que luego queda ocupando un
puerto local durante minutos (TIME_WAIT). A cientos de llamadas por segundo esos puertos se agotan (unos 16 000 en Windows,
unos 28 000 en Linux) y el servicio deja de poder hablar con los demás aunque ninguno esté caído. Una sesión compartida con
un pool de conexiones evita ese gasto y además se ahorra el saludo TCP en cada llamada.

Dos precauciones que ``requests.Session`` no toma por sí sola:

* **Sin cookies.** Una sesión compartida las guardaría y las reenviaría a otras personas. Aquí nunca se guardan.
* **Una conexión reutilizada puede haber sido cerrada por el otro lado justo antes de usarla.** Las lecturas
  (GET/HEAD) se repiten una vez en ese caso; las escrituras no, porque podrían ejecutarse dos veces.
"""

from __future__ import annotations

from http import cookiejar

import requests
from requests.adapters import HTTPAdapter

DEFAULT_MAX_CONNECTIONS = 32
_SAFE_TO_REPEAT = frozenset({"GET", "HEAD"})


class _NoCookies(cookiejar.CookiePolicy):
    """Política que rechaza todas las cookies (ni se guardan ni se envían)."""

    netscape = True
    rfc2965 = False
    hide_cookie2 = False

    def set_ok(self, cookie, request) -> bool:
        return False

    def return_ok(self, cookie, request) -> bool:
        return False

    def domain_return_ok(self, domain, request) -> bool:
        return False

    def path_return_ok(self, path, request) -> bool:
        return False


class _ReusingAdapter(HTTPAdapter):
    def send(self, request, **kwargs):
        try:
            return super().send(request, **kwargs)
        except requests.exceptions.ConnectionError as exc:
            # Un tiempo de conexión agotado no se repite (duplicaría la espera); una conexión cerrada por el servidor sí.
            if request.method not in _SAFE_TO_REPEAT or isinstance(exc, requests.exceptions.ConnectTimeout):
                raise
            return super().send(request, **kwargs)


def service_session(max_connections: int = DEFAULT_MAX_CONNECTIONS) -> requests.Session:
    """Sesión compartible entre hilos con un pool de hasta ``max_connections`` conexiones por servicio.

    Si hay más llamadas simultáneas que conexiones, las sobrantes abren una conexión temporal (no se bloquean).
    """
    session = requests.Session()
    session.cookies.set_policy(_NoCookies())
    adapter = _ReusingAdapter(pool_maxsize=max_connections, max_retries=0, pool_block=False)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
