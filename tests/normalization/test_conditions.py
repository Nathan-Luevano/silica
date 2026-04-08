from __future__ import annotations

from pysilica.analyze.normalize import normalize_conditions


def test_normalize_condition_hs_to_cs() -> None:
    text = "b.hs #16"
    res, mod = normalize_conditions(text)
    assert mod is True
    assert res == "b.cs #16"


def test_normalize_condition_lo_to_cc() -> None:
    text = "b.lo #32"
    res, mod = normalize_conditions(text)
    assert mod is True
    assert res == "b.cc #32"


def test_normalize_condition_eq_untouched() -> None:
    text = "b.eq #8"
    res, mod = normalize_conditions(text)
    assert mod is False
    assert res == "b.eq #8"
