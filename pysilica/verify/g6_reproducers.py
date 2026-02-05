from __future__ import annotations

from pathlib import Path

from pysilica.verify.types import VerifyResult

REPRODUCERS = Path("artifacts/reproducers")
MIN_REPRODUCERS = 10


def verify_g6_reproducers() -> VerifyResult:
    if not REPRODUCERS.is_dir():
        return VerifyResult("G6", False, {"missing": str(REPRODUCERS)}, {})
    found = list(REPRODUCERS.glob("*.md"))
    passed = len(found) >= MIN_REPRODUCERS
    return VerifyResult(
        "G6", passed,
        {"reproducer_files": [str(p) for p in found]},
        {"count": len(found), "required": MIN_REPRODUCERS},
    )
