"""Cabeceras de caché de las respuestas JSON."""


def public_cache(resp, seconds: int = 60):
    resp.headers["Cache-Control"] = f"public, max-age={seconds}"
    return resp


def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp
