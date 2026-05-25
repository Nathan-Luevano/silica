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

    # the real spec condition for this alias is "Rn == '11111'" (the FIRST
    # source register), not "either source is zr" -- "orr w0, w1, wzr" has
    # Rm (not Rn) as zr, which the actual ARM alias table does not cover,
    # so the spec-driven engine correctly declines rather than collapsing it
    res2 = norm.normalize("orr w0, w1, wzr")
    assert res2.normalized == "orr w0, w1, wzr"
    assert RULE_SPEC_ALIAS not in res2.applied_rules


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


def test_spec_alias_generic_engine_covers_cset_cinc() -> None:
    # CSINC -> CSET/CINC were never in the old hardcoded 10-mnemonic list;
    # the generic engine should pick them up straight from the loaded table
    out, changed = normalize_spec_alias("csinc w0, wzr, wzr, eq")
    assert changed
    assert out == "cset w0, eq"

    out2, changed2 = normalize_spec_alias("csinc w0, w1, w1, eq")
    assert changed2
    assert out2 == "cinc w0, w1, eq"


def test_spec_alias_generic_engine_covers_ldadd_family() -> None:
    # another mnemonic absent from the old hardcoded list -- confirms the
    # generic engine now walks all 71 loaded mnemonics, not just 10
    out, changed = normalize_spec_alias("ldadd w0, wzr, [x1]")
    assert changed
    assert out == "stadd w0, [x1]"


def test_spec_alias_generic_engine_covers_extr_ror() -> None:
    out, changed = normalize_spec_alias("extr w0, w1, w1, #5")
    assert changed
    assert out == "ror w0, w1, #5"


def test_load_spec_aliases_from_artifact() -> None:
    aliases = load_spec_aliases()
    assert isinstance(aliases, dict)
    assert "ORR" in aliases
    assert "SUBS" in aliases
    assert any(a.get("alias_mnemonic") == "MOV" for a in aliases["ORR"])
