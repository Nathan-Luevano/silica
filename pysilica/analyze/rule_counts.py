from __future__ import annotations

from pathlib import Path

from pysilica.analyze.normalize import Normalizer, RuleTracker

SAMPLE_PAIRS = [
    ("  ADD X0, X1, X2  ", "add x0, x1, x2"),
    ("SUB X0, X1, #0x10", "sub x0, x1, #16"),
    ("ORR W0, W31, W1", "orr w0, wzr, w1"),
    ("ADD X0, X1, X2, LSL #0", "add x0, x1, x2"),
    ("B.HS #16", "b.cs #16"),
    ("B.LO #32", "b.cc #32"),
    ("LDR X0, [X1, #0]", "ldr x0, [x1]"),
    ("RET // return to caller", "ret"),
    ("MOV X0, X1", "mov x0, x1"),
    ("NOP ; no operation", "nop"),
]


def generate_rule_counts(out_path: str = "artifacts/normalization_rule_counts.json") -> dict[str, object]:
    normalizer = Normalizer()
    tracker = RuleTracker()

    for raw, expected_target in SAMPLE_PAIRS:
        res = normalizer.normalize(raw)
        collapsed = res.normalized == expected_target
        tracker.record(res.applied_rules, collapsed=collapsed)

    p = Path(out_path)
    tracker.write_json(p)
    return tracker.to_dict()
