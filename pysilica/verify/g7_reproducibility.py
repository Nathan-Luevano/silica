from __future__ import annotations

from pathlib import Path

from pysilica.verify.types import VerifyResult

RESULT_HASH = Path("artifacts/result_hash.txt")


def verify_g7_reproducibility() -> VerifyResult:
    if not RESULT_HASH.exists():
        return VerifyResult("G7", False, {"missing": str(RESULT_HASH)}, {})
    return VerifyResult(
        "G7", False,
        {"reason": "clean-checkout reproduction not yet verified"},
        {},
    )
