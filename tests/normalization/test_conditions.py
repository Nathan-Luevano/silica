from __future__ import annotations

from pysilica.analyze.normalize import RULE_CONDITIONS, Normalizer, normalize_conditions


def test_condition_codes_hs_to_cs() -> None:
    norm = Normalizer()
    res = norm.normalize("b.hs #16")
    assert res.normalized == "b.cs #16"
    assert RULE_CONDITIONS in res.applied_rules


def test_condition_codes_lo_to_cc() -> None:
    norm = Normalizer()
    res = norm.normalize("b.lo #32")
    assert res.normalized == "b.cc #32"
    assert RULE_CONDITIONS in res.applied_rules


def test_normalize_condition_codes_function() -> None:
    res, mod = normalize_conditions("b.hs #16")
    assert mod is True
    assert res == "b.cs #16"

    res, mod = normalize_conditions("b.lo #32")
    assert mod is True
    assert res == "b.cc #32"

    res, mod = normalize_conditions("b.eq #8")
    assert mod is False
    assert res == "b.eq #8"
