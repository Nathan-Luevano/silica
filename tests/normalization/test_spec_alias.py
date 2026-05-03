from __future__ import annotations

from pysilica.analyze.normalize import (
    RULE_SPEC_ALIAS,
    Normalizer,
    load_spec_aliases,
    normalize_spec_alias,
)


def test_spec_alias_mov_orr() -> None:
    norm = Normalizer()
    res = norm.normalize("orr x0, xzr, x1")
    assert res.normalized == "mov x0, x1"
    assert RULE_SPEC_ALIAS in res.applied_rules

    res2 = norm.normalize("orr w0, w1, wzr")
    assert res2.normalized == "mov w0, w1"
    assert RULE_SPEC_ALIAS in res2.applied_rules


def test_spec_alias_cmp_subs() -> None:
    norm = Normalizer()
    res = norm.normalize("subs xzr, x0, x1")
    assert res.normalized == "cmp x0, x1"
    assert RULE_SPEC_ALIAS in res.applied_rules

    res2 = norm.normalize("subs wzr, w0, #16")
    assert res2.normalized == "cmp w0, #16"
    assert RULE_SPEC_ALIAS in res2.applied_rules


def test_spec_alias_cmn_adds() -> None:
    norm = Normalizer()
    res = norm.normalize("adds xzr, x0, x1")
    assert res.normalized == "cmn x0, x1"
    assert RULE_SPEC_ALIAS in res.applied_rules


def test_spec_alias_tst_ands() -> None:
    norm = Normalizer()
    res = norm.normalize("ands xzr, x0, x1")
    assert res.normalized == "tst x0, x1"
    assert RULE_SPEC_ALIAS in res.applied_rules


def test_spec_alias_mvn_orn() -> None:
    norm = Normalizer()
    res = norm.normalize("orn x0, xzr, x1")
    assert res.normalized == "mvn x0, x1"
    assert RULE_SPEC_ALIAS in res.applied_rules


def test_spec_alias_neg_sub() -> None:
    norm = Normalizer()
    res = norm.normalize("sub x0, xzr, x1")
    assert res.normalized == "neg x0, x1"
    assert RULE_SPEC_ALIAS in res.applied_rules

    res2 = norm.normalize("subs x0, xzr, x1")
    assert res2.normalized == "negs x0, x1"
    assert RULE_SPEC_ALIAS in res2.applied_rules


def test_normalize_spec_alias_function_direct() -> None:
    out, changed = normalize_spec_alias("orr x0, xzr, x1")
    assert changed
    assert out == "mov x0, x1"

    out, changed = normalize_spec_alias("add x0, x1, x2")
    assert not changed
    assert out == "add x0, x1, x2"


def test_load_spec_aliases_from_artifact() -> None:
    aliases = load_spec_aliases()
    assert isinstance(aliases, dict)
    assert "ORR" in aliases
    assert "SUBS" in aliases
    assert any(a.get("alias_mnemonic") == "MOV" for a in aliases["ORR"])
