from __future__ import annotations

from pysilica.analyze.normalize import (
    RULE_LOWERCASE,
    RULE_WHITESPACE,
    normalize_case,
    normalize_whitespace,
)


def test_normalize_whitespace_basic() -> None:
    text = "  add   x0,   x1,   x2  "
    res, mod = normalize_whitespace(text)
    assert mod is True
    assert res == "add x0, x1, x2"


def test_normalize_whitespace_brackets() -> None:
    text = "ldr x0, [ x1, #0 ]"
    res, mod = normalize_whitespace(text)
    assert mod is True
    assert res == "ldr x0, [x1, #0]"


def test_normalize_case_lowercase() -> None:
    text = "ADD X0, X1, #16"
    res, mod = normalize_case(text)
    assert mod is True
    assert res == "add x0, x1, #16"
    assert RULE_LOWERCASE == "lowercase"
    assert RULE_WHITESPACE == "whitespace"
