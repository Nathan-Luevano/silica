from __future__ import annotations

from pysilica.analyze.normalize import normalize_shift_defaults


def test_normalize_shift_defaults_lsl0() -> None:
    text = "add x0, x1, x2, lsl #0"
    res, mod = normalize_shift_defaults(text)
    assert mod is True
    assert res == "add x0, x1, x2"


def test_normalize_shift_defaults_lsl_nonzero() -> None:
    text = "add x0, x1, x2, lsl #2"
    res, mod = normalize_shift_defaults(text)
    assert mod is False
    assert res == "add x0, x1, x2, lsl #2"
