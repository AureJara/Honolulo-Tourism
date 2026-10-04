"""Límite de peticiones por IP: el almacén, las reglas y su integración en la pasarela web."""

import re

import pytest

from app import create_app
from app.ratelimit import MemoryStore, RateLimiter, Rule, default_rules

SECRET = "test-web-secret-test-web-secret-123"
XHR = {"X-Requested-With": "XMLHttpRequest"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_app(**overrides):
    return create_app({"TESTING": True, "SECRET_KEY": SECRET, "RATE_LIMIT_ENABLED": True, **overrides})


def client_for(app, ip="10.0.0.1"):
    return app.test_client(), {"REMOTE_ADDR": ip}


# ------------------------------------------------------------------ el almacén
def test_store_allows_up_to_the_limit_then_reports_the_wait():
    clock, store = Clock(), None
    store = MemoryStore(clock)
    assert [store.hit("k", 3, 60) for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = store.hit("k", 3, 60)
    assert 59 < wait <= 60
    clock.now += 30
    assert 29 < store.hit("k", 3, 60) <= 30                 # sigue bloqueado y el tiempo restante baja


def test_store_recovers_when_the_window_passes():
    clock = Clock()
    store = MemoryStore(clock)
    for _ in range(3):
        store.hit("k", 3, 60)
    assert store.hit("k", 3, 60) > 0
    clock.now += 61
    assert store.hit("k", 3, 60) == 0.0


def test_store_is_a_sliding_window_not_a_fixed_one():
    clock = Clock()
    store = MemoryStore(clock)
    store.hit("k", 2, 60)                                   # t=0
    clock.now += 40
    store.hit("k", 2, 60)                                   # t=40
    clock.now += 25                                         # t=65: el primero ya salió de la ventana
    assert store.hit("k", 2, 60) == 0.0
    assert store.hit("k", 2, 60) > 0


def test_store_keys_are_independent():
    store = MemoryStore(Clock())
    for _ in range(2):
        store.hit("a", 2, 60)
    assert store.hit("a", 2, 60) > 0 and store.hit("b", 2, 60) == 0.0


def test_store_drops_expired_keys_so_memory_does_not_grow_forever():
    clock = Clock()
    store = MemoryStore(clock)
    for i in range(300):
        store.hit(f"ip-{i}", 5, 60)
    assert len(store) == 300
    clock.now += 120
    for _ in range(MemoryStore.PRUNE_EVERY):
        store.hit("vivo", 1000, 60)
    assert len(store) == 1


# ------------------------------------------------------------------- las reglas
def test_rules_match_by_method_and_path():
    rules = {r.name: r for r in default_rules()}
    assert rules["login"].matches("POST", "/ingresar") and not rules["login"].matches("GET", "/ingresar")
    assert rules["review-write"].matches("PUT", "/api/v1/places/cascada-san-miguel/reviews/mine")
    assert not rules["review-write"].matches("GET", "/api/v1/places/cascada-san-miguel/reviews/mine")
    assert rules["moderation"].matches("DELETE", "/api/v1/reviews/abc") and rules["moderation"].matches("POST", "/admin/opiniones/x/fijar")
    assert rules["global"].matches("GET", "/") and rules["global"].matches("GET", "/api/v1/places")
    assert not rules["global"].matches("GET", "/static/js/app.js") and not rules["global"].matches("GET", "/media/abc.webp")


def test_limiter_applies_every_matching_rule_and_reports_the_longest_wait():
    clock = Clock()
    rules = [Rule("corta", 1, 10, re.compile("^/x$"), "corta"), Rule("larga", 1, 100, re.compile("^/x$"), "larga")]
    limiter = RateLimiter(rules, MemoryStore(clock))
    assert limiter.check("GET", "/x", "ip") is None
    violation = limiter.check("GET", "/x", "ip")
    assert violation.rule.name == "larga" and violation.retry_after > 90
    assert limiter.check("GET", "/otra", "ip") is None


def test_a_disabled_limiter_never_blocks():
    limiter = RateLimiter([Rule("r", 1, 60, re.compile("."), "m")], MemoryStore(Clock()), enabled=False)
    assert all(limiter.check("GET", "/", "ip") is None for _ in range(50))


def test_a_new_rule_can_be_added_without_touching_the_limiter():
    limiter = RateLimiter([], MemoryStore(Clock()))
    assert limiter.check("GET", "/secreto", "ip") is None
    limiter.add(Rule("secreto", 1, 60, re.compile("^/secreto$"), "no tanto"))
    assert limiter.check("GET", "/secreto", "ip") is None and limiter.check("GET", "/secreto", "ip") is not None


# -------------------------------------------------------------- en la pasarela web
def test_login_attempts_are_limited_per_ip_with_a_clear_page():
    app = make_app()
    client, env = client_for(app)
    statuses = [client.post("/ingresar", data={}, environ_overrides=env).status_code for _ in range(10)]
    assert 429 not in statuses
    blocked = client.post("/ingresar", data={}, environ_overrides=env)
    assert blocked.status_code == 429 and blocked.mimetype == "text/html"
    assert int(blocked.headers["Retry-After"]) >= 1
    html = blocked.get_data(as_text=True)
    assert "Demasiados intentos de ingreso" in html and "Demasiadas solicitudes" in html


def test_other_ips_and_other_pages_are_not_affected():
    app = make_app()
    client, env = client_for(app, "10.0.0.1")
    for _ in range(11):
        client.post("/ingresar", data={}, environ_overrides=env)
    assert client.post("/ingresar", data={}, environ_overrides=env).status_code == 429
    assert client.get("/ingresar", environ_overrides=env).status_code == 200          # solo se limitan los intentos (POST)
    assert client.get("/politica-de-cookies", environ_overrides=env).status_code == 200
    other = client.post("/ingresar", data={}, environ_overrides={"REMOTE_ADDR": "10.0.0.2"})
    assert other.status_code != 429


def test_api_limits_answer_with_problem_json_and_retry_after():
    app = make_app()
    client, env = client_for(app)
    path = "/api/v1/places/cascada-san-miguel/reviews/mine"
    statuses = [client.put(path, json={"rating": 5}, headers=XHR, environ_overrides=env).status_code for _ in range(20)]
    assert set(statuses) == {401}                                        # sin sesión, pero cuentan como intentos
    blocked = client.put(path, json={"rating": 5}, headers=XHR, environ_overrides=env)
    body = blocked.get_json()
    assert blocked.status_code == 429 and blocked.mimetype == "application/problem+json"
    assert body["code"] == "RATE_LIMITED" and body["retry_after"] >= 1 and int(blocked.headers["Retry-After"]) == body["retry_after"]


def test_verification_code_guessing_is_limited():
    app = make_app()
    client, env = client_for(app)
    for _ in range(15):
        client.post("/verificar", data={}, environ_overrides=env)
    assert client.post("/verificar", data={}, environ_overrides=env).status_code == 429


def test_the_global_limit_stops_floods_but_not_static_files():
    app = make_app()
    client, env = client_for(app)
    assert all(client.get("/static/css/app.css", environ_overrides=env).status_code == 200 for _ in range(320))
    codes = [client.get("/politica-de-cookies", environ_overrides=env).status_code for _ in range(305)]
    assert codes[:300] == [200] * 300 and 429 in codes[300:]


def test_x_forwarded_for_cannot_be_used_to_dodge_the_limit_by_default():
    app = make_app()
    client, env = client_for(app)
    codes = [client.post("/ingresar", data={}, environ_overrides=env, headers={"X-Forwarded-For": f"9.9.9.{i}"}).status_code
             for i in range(12)]
    assert codes[-1] == 429                                              # los 12 intentos cuentan para la misma IP real


def test_behind_a_trusted_proxy_the_forwarded_ip_is_used():
    app = make_app(TRUSTED_PROXY_HOPS=1)
    client, env = client_for(app, "172.16.0.5")                          # la IP del proxy
    for _ in range(11):
        client.post("/ingresar", data={}, environ_overrides=env, headers={"X-Forwarded-For": "203.0.113.7"})
    assert client.post("/ingresar", data={}, environ_overrides=env, headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 429
    other = client.post("/ingresar", data={}, environ_overrides=env, headers={"X-Forwarded-For": "203.0.113.8"})
    assert other.status_code != 429                                      # otra persona detrás del mismo proxy sigue pudiendo


def test_the_limiter_can_be_turned_off_for_automated_qa():
    app = make_app(RATE_LIMIT_ENABLED=False)
    client, env = client_for(app)
    assert all(client.post("/ingresar", data={}, environ_overrides=env).status_code != 429 for _ in range(40))


def test_rate_limited_requests_do_not_reach_any_microservice():
    import responses
    app = make_app()
    client, env = client_for(app)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        for _ in range(12):
            client.post("/ingresar", data={}, environ_overrides=env)
        assert len(rsps.calls) == 0                                      # sin CSRF ni límite: nunca se llama a `auth`


def test_the_limiter_is_exposed_so_rules_can_be_added_at_runtime():
    app = make_app()
    app.extensions["rate_limiter"].add(Rule("prueba", 2, 60, re.compile("^/politica-de-cookies$"), "Solo dos veces."))
    client, env = client_for(app)
    assert [client.get("/politica-de-cookies", environ_overrides=env).status_code for _ in range(3)] == [200, 200, 429]
