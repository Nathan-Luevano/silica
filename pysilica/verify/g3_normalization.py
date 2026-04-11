from __future__ import annotations

import json
import re
from pathlib import Path

from pysilica.verify.types import VerifyResult

RULE_TESTS = Path("tests/normalization")
RULE_COUNTS = Path("artifacts/normalization_rule_counts.json")

REQUIRED_COUNT_KEYS = {"total_comparisons", "collapsed_disagreements", "rule_counts"}


def _rule_has_dedicated_test(rule_name: str, test_sources: str) -> bool:
    # a rule "lands with a test pinning its exact behavior" (design.md §7
    # rule 1) if some test file's content actually names it - cheap but
    # real: catches a rule added to rule_counts with no test written for it.
    needle = rule_name.replace("_", "")
    haystack = re.sub(r"[^a-z0-9]", "", test_sources.lower())
    return needle in haystack


def verify_g3_normalization() -> VerifyResult:
    if not RULE_TESTS.is_dir():
        return VerifyResult("G3", False, {"missing": str(RULE_TESTS)}, {})

    test_files = sorted(RULE_TESTS.glob("test_*.py"))
    if not test_files:
        return VerifyResult("G3", False, {"missing": f"{RULE_TESTS}/test_*.py"}, {})

    if not RULE_COUNTS.exists():
        return VerifyResult("G3", False, {"missing": str(RULE_COUNTS)}, {})

    counts = json.loads(RULE_COUNTS.read_text())
    missing_keys = REQUIRED_COUNT_KEYS - counts.keys()
    if missing_keys:
        return VerifyResult("G3", False, {"missing_count_keys": sorted(missing_keys)}, {})

    rule_counts = counts["rule_counts"]
    if not isinstance(rule_counts, dict) or not rule_counts:
        return VerifyResult("G3", False, {"reason": "rule_counts is empty or not an object"}, {})

    measured: dict[str, object] = {
        "total_comparisons": counts["total_comparisons"],
        "collapsed_disagreements": counts["collapsed_disagreements"],
        "declared_rules": sorted(rule_counts.keys()),
    }

    if counts["total_comparisons"] <= 0:
        return VerifyResult("G3", False, {"reason": "total_comparisons must be > 0"}, measured)

    # design.md §7: "Alias handling uses the spec's <alias_list>, never
    # hand-written lists." operationalized as: alias collapsing must be a
    # distinct, separately-counted rule - not folded silently into
    # formatting - so a missing alias rule is visible here, not hidden.
    has_alias_rule = any("alias" in name.lower() for name in rule_counts)
    if not has_alias_rule:
        return VerifyResult(
            "G3", False,
            {"reason": "no rule_counts entry matching 'alias' - §7 requires spec-alias_list-driven alias handling as its own tracked rule"},
            measured,
        )

    test_sources = "\n".join(f.read_text() for f in test_files)
    untested_rules = [name for name in rule_counts if not _rule_has_dedicated_test(name, test_sources)]
    if untested_rules:
        return VerifyResult(
            "G3", False,
            {"reason": "declared rule(s) with no matching dedicated test", "untested_rules": sorted(untested_rules)},
            measured,
        )

    return VerifyResult("G3", True, {"rule_test_files": [str(f) for f in test_files]}, measured)
