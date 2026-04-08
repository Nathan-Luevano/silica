from __future__ import annotations

from pysilica.analyze.normalize import normalize_immediates


def test_normalize_hex_immediates() -> None:
    text = "add x0, x1, #0x10"
    res, mod = normalize_immediates(text)
    assert mod is True
    assert res == "add x0, x1, #16"


def test_normalize_hex_immediates_large() -> None:
    text = "mov x0, #0x1000"
    res, mod = normalize_immediates(text)
    assert mod is True
    assert res == "mov x0, #4096"


def test_normalize_spaced_immediates() -> None:
    text = "sub x0, x1, # 32"
    res, mod = normalize_immediates(text)
    assert mod is True
    assert res == "sub x0, x1, #32"
