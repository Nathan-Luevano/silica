from __future__ import annotations

from pysilica.analyze.normalize import normalize_case, normalize_whitespace


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


def test_normalize_case() -> None:
    text = "ADD X0, X1, #16"
    res, mod = normalize_case(text)
    assert mod is True
    assert res == "add x0, x1, #16"
