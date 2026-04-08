from __future__ import annotations

from pysilica.analyze.normalize import normalize_memory_operands


def test_normalize_memory_zero_offset() -> None:
    text = "ldr x0, [x1, #0]"
    res, mod = normalize_memory_operands(text)
    assert mod is True
    assert res == "ldr x0, [x1]"


def test_normalize_memory_nonzero_offset() -> None:
    text = "ldr x0, [x1, #8]"
    res, mod = normalize_memory_operands(text)
    assert mod is False
    assert res == "ldr x0, [x1, #8]"
