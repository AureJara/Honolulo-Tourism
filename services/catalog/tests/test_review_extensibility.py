"""Principio abierto/cerrado en las opiniones: una norma o un saneador nuevo se añade con un decorador, sin modificar
el módulo de validación ni las rutas."""

import pytest

from app import review_schemas as rs
from honolulo_common.errors import ApiError

MAX = 1000


@pytest.fixture()
def registries(monkeypatch):
    monkeypatch.setattr(rs, "_RULES", list(rs._RULES))
    monkeypatch.setattr(rs, "_SANITIZERS", list(rs._SANITIZERS))


def reject(text):
    with pytest.raises(ApiError) as exc:
        rs.normalize_comment(text, MAX)
    return exc.value.extra["errors"][0]["message"] if getattr(exc.value, "extra", None) else str(exc.value)


def test_normalize_comment_default_behaviour():
    assert rs.normalize_comment(None, MAX) is None
    assert rs.normalize_comment("   \n ", MAX) is None
    assert rs.normalize_comment("  Hola\u200b  mundo\x00  ", MAX) == "Hola  mundo"
    assert rs.normalize_comment("a\n\n\n\n\nb", MAX) == "a\n\nb"
    assert rs.normalize_comment("x" * MAX, MAX) == "x" * MAX


@pytest.mark.parametrize("text", ["ab", "x" * (MAX + 1), "mira www.sitio.com", "x" * (MAX * 3 + 1)])
def test_default_rules_still_reject(text):
    with pytest.raises(ApiError) as exc:
        rs.normalize_comment(text, MAX)
    assert exc.value.status == 422 if hasattr(exc.value, "status") else True


def test_a_new_comment_rule_is_applied_without_editing_the_module(registries):
    @rs.comment_rule
    def _no_shouting(text, max_chars):
        letters = [c for c in text if c.isalpha()]
        return "No escribas todo en mayúsculas." if len(letters) >= 8 and all(c.isupper() for c in letters) else None

    assert rs.normalize_comment("Una cascada preciosa", MAX) == "Una cascada preciosa"
    with pytest.raises(ApiError):
        rs.normalize_comment("QUE CASCADA TAN BONITA", MAX)


def test_a_new_sanitizer_is_applied_before_the_rules(registries):
    @rs.comment_sanitizer
    def _no_tabs(text):
        return text.replace("\t", " ")

    assert rs.normalize_comment("a\tb", MAX) == "a b"


def test_rules_see_the_sanitized_text_not_the_raw_one(registries):
    seen = []

    @rs.comment_rule
    def _spy(text, max_chars):
        seen.append(text)
        return None

    rs.normalize_comment("  hola\u200b mundo  ", MAX)
    assert seen == ["hola mundo"]
