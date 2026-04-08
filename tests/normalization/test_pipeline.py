from __future__ import annotations

from pysilica.analyze.normalize import Normalizer, RuleTracker


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
