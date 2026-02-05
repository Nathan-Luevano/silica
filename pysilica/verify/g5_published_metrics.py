from __future__ import annotations

from pathlib import Path

from pysilica.verify.types import VerifyResult

REPORT = Path("artifacts/report/metrics.json")


def verify_g5_published_metrics() -> VerifyResult:
    if not REPORT.exists():
        return VerifyResult("G5", False, {"missing": str(REPORT)}, {})
    return VerifyResult(
        "G5", False,
        {"reason": "per-tool macro/micro agreement rates not yet verified"},
        {},
    )
