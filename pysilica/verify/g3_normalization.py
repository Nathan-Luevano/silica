from __future__ import annotations

from pathlib import Path

from pysilica.verify.types import VerifyResult

RULE_TESTS = Path("tests/normalization")
RULE_COUNTS = Path("artifacts/normalization_rule_counts.json")


def verify_g3_normalization() -> VerifyResult:
    if not RULE_TESTS.is_dir() or not any(RULE_TESTS.glob("test_*.py")):
        return VerifyResult("G3", False, {"missing": str(RULE_TESTS)}, {})
    if not RULE_COUNTS.exists():
        return VerifyResult("G3", False, {"missing": str(RULE_COUNTS)}, {})
    return VerifyResult(
        "G3", False,
        {"reason": "per-rule test-to-implementation binding not yet verified"},
        {},
    )
