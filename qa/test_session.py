"""Ciclo de vida de la sesión en el navegador: renovación transparente, concurrencia y varios dispositivos."""

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from conftest import BASE, Web


def drop_cookie(web: Web, name: str) -> None:
    for c in list(web.s.cookies):
        if c.name == name:
            web.s.cookies.clear(c.domain, c.path, c.name)


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def test_expired_access_cookie_is_renewed_transparently(member):
    w = Web()
    w.login(member["email"])
    old = w.s.cookies.get("hn_access")
    drop_cookie(w, "hn_access")                                     # el navegador la borra al vencer (Max-Age)
    resp = w.api("GET", "/api/v1/weather/current")
    assert resp.status_code == 200
    new = w.s.cookies.get("hn_access")
    assert new and new != old                                        # se emitió un token nuevo
    assert w.get("/").status_code == 200


def test_parallel_requests_with_an_expired_token_rotate_the_refresh_token_only_once(member):
    """Varias pestañas/peticiones a la vez con el access vencido: ninguna debe perder la sesión."""
    w = Web()
    w.login(member["email"])
    refresh = w.s.cookies.get("hn_refresh")

    def call(_):
        s = requests.Session()
        s.cookies.set("hn_refresh", refresh, domain="127.0.0.1", path="/")
        r = s.get(BASE + "/api/v1/weather/history?page_size=1", timeout=30)
        return r.status_code, s.cookies.get("hn_access"), s.cookies.get("hn_refresh")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(call, range(8)))
    assert [r[0] for r in results] == [200] * 8, results
    new_refresh = {r[2] for r in results}
    assert len(new_refresh) == 1 and refresh not in new_refresh     # todas recibieron la misma rotación
    follow = requests.Session()
    follow.cookies.set("hn_refresh", new_refresh.pop(), domain="127.0.0.1", path="/")
    assert follow.get(BASE + "/api/v1/weather/current", timeout=30).status_code in (200,)


def test_session_cookies_lifetimes(member):
    w = Web()
    resp = w.login(member["email"])
    cookies = {c.split("=", 1)[0]: c for c in resp.raw.headers.getlist("Set-Cookie")}
    age = lambda name: int(re.search(r"Max-Age=(\d+)", cookies[name]).group(1))
    access_age, refresh_age = age("hn_access"), age("hn_refresh")
    assert "HttpOnly" in cookies["hn_access"] and "HttpOnly" in cookies["hn_refresh"]
    assert access_age == 15 * 60 and refresh_age == 7 * 24 * 3600
    token = claims(w.s.cookies.get("hn_access"))
    assert token["exp"] - token["iat"] == 15 * 60 and token["type"] == "access" and token["aud"] == "honolulo-api"
    assert "password" not in token and token["role"] in ("user", "admin")


def test_logging_out_on_one_device_keeps_the_other_device_signed_in(member):
    phone, laptop = Web(), Web()
    phone.login(member["email"])
    laptop.login(member["email"])
    assert phone.form("/salir", {}, csrf_path="/").status_code == 302
    assert phone.api("GET", "/api/v1/weather/current").status_code == 401
    assert laptop.api("GET", "/api/v1/weather/current").status_code == 200


def test_login_page_redirects_signed_in_users_and_keeps_safe_next(member):
    w = Web()
    w.login(member["email"])
    resp = w.get("/ingresar?next=/admin/lugares")
    assert resp.status_code == 302 and resp.headers["Location"] == "/admin/lugares"
    assert w.get("/ingresar?next=//evil.com").headers["Location"] == "/"


def test_revoked_refresh_token_forces_a_clean_logout_in_the_browser(member):
    w = Web()
    w.login(member["email"])
    refresh = w.s.cookies.get("hn_refresh")
    requests.post("http://127.0.0.1:5001/api/v1/auth/logout", json={"refresh_token": refresh}, timeout=10)   # revocado en otro lado
    drop_cookie(w, "hn_access")
    resp = w.api("GET", "/api/v1/weather/current")
    assert resp.status_code == 401
    page = w.get("/")
    assert page.status_code == 302 and page.headers["Location"].startswith("/ingresar")
