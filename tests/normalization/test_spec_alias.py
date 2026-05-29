from __future__ import annotations

from pysilica.analyze.normalize import (
    RULE_SPEC_ALIAS,
    Normalizer,
    load_spec_aliases,
    normalize_spec_alias,
)


def test_spec_alias_mov_orr() -> None:
    norm = Normalizer()

    # the real spec conditions for ORR -> MOV are all compound:
    # "Rn == '11111' && !MoveWidePreferred(sf, N, imms, immr)" (log_imm form)
    # and "shift == '00' && imm6 == '000000' && Rn == '11111'" (log_shift
    # form). Both have a clause (a function call, a non-register field) this
    # engine cannot verify from text, so per design.md sec7 rule 2 it must
    # decline the whole alias rather than collapse on the Rn == '11111'
    # clause alone -- that partial check is exactly the bug this suite is
    # pinning against (see test_spec_alias_bfm_bfc_requires_full_condition).
    res = norm.normalize("orr x0, xzr, x1")
    assert res.normalized == "orr x0, xzr, x1"
    assert RULE_SPEC_ALIAS not in res.applied_rules

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
    # ORR -> MOV's real condition has a !MoveWidePreferred(...) clause this
    # engine can't verify from text, so it must decline (see
    # test_spec_alias_mov_orr for the full explanation).
    out, changed = normalize_spec_alias("orr x0, xzr, x1")
    assert not changed
    assert out == "orr x0, xzr, x1"

    out, changed = normalize_spec_alias("add x0, x1, x2")
    assert not changed
    assert out == "add x0, x1, x2"


def test_spec_alias_generic_engine_covers_cset_cinc() -> None:
    # CSINC -> CSET/CINC's real conditions both carry a
    # "!(cond IN {'111x'})" clause -- a condition-code-set membership test
    # this engine has no way to check from normalized text -- so both must
    # be declined, not collapsed on the Rm/Rn == '11111' clauses alone.
    out, changed = normalize_spec_alias("csinc w0, wzr, wzr, eq")
    assert not changed
    assert out == "csinc w0, wzr, wzr, eq"

    out2, changed2 = normalize_spec_alias("csinc w0, w1, w1, eq")
    assert not changed2
    assert out2 == "csinc w0, w1, w1, eq"


def test_spec_alias_generic_engine_covers_ldadd_family() -> None:
    # LDADD -> STADD's real condition is "A == '0' && Rt == '11111'" -- the
    # acquire-semantics bit A is not visible in normalized disassembly text
    # at all, so this must decline rather than collapse on Rt == '11111'
    # alone.
    out, changed = normalize_spec_alias("ldadd w0, wzr, [x1]")
    assert not changed
    assert out == "ldadd w0, wzr, [x1]"


def test_spec_alias_generic_engine_covers_extr_ror() -> None:
    out, changed = normalize_spec_alias("extr w0, w1, w1, #5")
    assert changed
    assert out == "ror w0, w1, #5"


def test_spec_alias_bfm_bfc_requires_full_condition() -> None:
    # BFM's real BFC condition is "Rn == '11111' && UInt(imms) < UInt(immr)".
    # imms=8, immr=4 (decimal) means UInt(imms) < UInt(immr) is FALSE (8 is
    # not < 4), so this specific word must NOT collapse to BFC even though
    # Rn is zr -- checking only the Rn clause and applying regardless of the
    # UInt clause was the exact bug this fix addresses (audit repro).
    out, changed = normalize_spec_alias("bfm x0, xzr, #4, #8")
    assert not changed
    assert out == "bfm x0, xzr, #4, #8"

    # same reasoning for BFM -> BFI: "Rn != '11111' && UInt(imms) < UInt(immr)"
    out2, changed2 = normalize_spec_alias("bfm x0, x1, #4, #8")
    assert not changed2
    assert out2 == "bfm x0, x1, #4, #8"


def test_spec_alias_ldadd_family_all_decline() -> None:
    # all 27 LDADD/LDCLR/LDEOR/LDSET/LDSMAX/LDSMIN/LDUMAX/LDUMIN (and byte/
    # halfword variants) alias entries share the same unverifiable-A-bit
    # condition; sweep the whole family instead of just one representative.
    aliases = load_spec_aliases()
    family = [
        base
        for base in aliases
        if base.startswith(("LDADD", "LDCLR", "LDEOR", "LDSET", "LDSMAX", "LDSMIN", "LDUMAX", "LDUMIN", "LDT"))
    ]
    assert len(family) == 27, f"expected 27 LDADD-family entries, found {len(family)}: {family}"
    for base in family:
        text = f"{base.lower()} w0, wzr, [x1]"
        out, changed = normalize_spec_alias(text, aliases)
        assert not changed, f"{base} incorrectly collapsed to {out!r}"
        assert out == text


def test_spec_alias_compound_but_fully_verifiable_still_collapses() -> None:
    # SUBS -> NEGS's condition "Rn == '11111' && Rd != '11111'" IS compound
    # (two &&-joined clauses) but BOTH clauses are shapes this engine can
    # verify from text -- proving the fix doesn't over-correct into
    # declining every compound condition, only the ones with a genuinely
    # unverifiable clause.
    out, changed = normalize_spec_alias("subs x0, xzr, x1")
    assert changed
    assert out == "negs x0, x1"

    # and the guard direction: Rd == '11111' too means it should stay SUBS
    # (routes to CMP via a different, simple alias entry instead)
    out2, changed2 = normalize_spec_alias("subs xzr, xzr, x1")
    assert changed2
    assert out2 == "cmp xzr, x1"


def test_load_spec_aliases_from_artifact() -> None:
    aliases = load_spec_aliases()
    assert isinstance(aliases, dict)
    assert "ORR" in aliases
    assert "SUBS" in aliases
    assert any(a.get("alias_mnemonic") == "MOV" for a in aliases["ORR"])
