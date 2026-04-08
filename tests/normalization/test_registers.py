from __future__ import annotations

from pysilica.analyze.normalize import normalize_zero_registers


def test_normalize_zero_registers_w31() -> None:
    text = "orr w0, w31, w1"
    res, mod = normalize_zero_registers(text)
    assert mod is True
    assert res == "orr w0, wzr, w1"


def test_normalize_zero_registers_x31() -> None:
    text = "add x0, x31, x2"
    res, mod = normalize_zero_registers(text)
    assert mod is True
    assert res == "add x0, xzr, x2"


def test_normalize_zero_registers_untouched_sp() -> None:
    text = "mov sp, x0"
    res, mod = normalize_zero_registers(text)
    assert mod is False
    assert res == "mov sp, x0"
