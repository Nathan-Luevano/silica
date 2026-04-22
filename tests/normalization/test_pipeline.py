from __future__ import annotations

from pysilica.analyze.normalize import (
    TAXONOMY_EQUIVALENT,
    TAXONOMY_NORMALIZATION_UNCERTAIN,
    TAXONOMY_OPERAND,
    TAXONOMY_VALIDITY,
    Normalizer,
    RuleTracker,
    classify_disagreement,
)


def test_normalizer_full_pipeline() -> None:
    normalizer = Normalizer()
    raw = "  ADD   X0,   X31,   #0x10,   LSL #0   // add immediate  "
    res = normalizer.normalize(raw)

    assert res.normalized == "add x0, xzr, #16"
    assert "strip_comments" in res.applied_rules
    assert "lowercase" in res.applied_rules
    assert "whitespace" in res.applied_rules
    assert "immediates" in res.applied_rules
    assert "zero_registers" in res.applied_rules
    assert "shift_defaults" in res.applied_rules


def test_rule_tracker() -> None:
    tracker = RuleTracker()
    tracker.record(["lowercase", "whitespace"], collapsed=True)
    tracker.record(["immediates"], collapsed=False)

    d = tracker.to_dict()
    assert d["total_comparisons"] == 2
    assert d["collapsed_disagreements"] == 1
    assert tracker.counts()["lowercase"] == 1
    assert tracker.counts()["whitespace"] == 1
    assert tracker.counts()["immediates"] == 1
    assert tracker.counts()["zero_registers"] == 0


def test_classify_disagreement_taxonomy() -> None:
    assert classify_disagreement(None, "nop", None, "nop") == TAXONOMY_VALIDITY
    assert classify_disagreement("nop", "nop", "nop", "nop") == TAXONOMY_EQUIVALENT
    assert classify_disagreement("add x0, x1, x2", "add x0, x1, x3", "add x0, x1, x2", "add x0, x1, x3") == TAXONOMY_OPERAND
    assert classify_disagreement("foo x0", "bar x0", "foo x0", "bar x0") == TAXONOMY_NORMALIZATION_UNCERTAIN
