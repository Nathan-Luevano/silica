from __future__ import annotations

import json
from pathlib import Path

from pysilica.verify.types import VerifyResult

ARTIFACTS = Path("artifacts")
DECODE_TABLE = ARTIFACTS / "decode-table.bin"
G1_METRICS = ARTIFACTS / "g1_metrics.json"

RET_WORD = 0xD65F03C0


def verify_g1_spec_oracle() -> VerifyResult:
    evidence: dict[str, object] = {}
    measured: dict[str, object] = {}

    if not DECODE_TABLE.exists():
        return VerifyResult(
            "G1", False, {"missing": str(DECODE_TABLE)}, {}
        )
    if not G1_METRICS.exists():
        return VerifyResult(
            "G1", False, {"missing": str(G1_METRICS)}, {}
        )

    metrics = json.loads(G1_METRICS.read_text())
    evidence["decode_table"] = str(DECODE_TABLE)
    evidence["decode_table_bytes"] = DECODE_TABLE.stat().st_size
    evidence["g1_metrics"] = str(G1_METRICS)

    required_keys = {"tiling_files_checked", "tiling_files_passed", "allocated", "unallocated", "spec_release"}
    missing_keys = required_keys - metrics.keys()
    if missing_keys:
        return VerifyResult("G1", False, {"missing_metric_keys": sorted(missing_keys)}, {})

    tiling_ok = metrics["tiling_files_checked"] == metrics["tiling_files_passed"] and metrics["tiling_files_checked"] > 0
    measured["tiling_files_checked"] = metrics["tiling_files_checked"]
    measured["tiling_files_passed"] = metrics["tiling_files_passed"]
    measured["allocated"] = metrics["allocated"]
    measured["unallocated"] = metrics["unallocated"]
    measured["spec_release"] = metrics["spec_release"]

    if metrics["spec_release"] != "ISA_A64_xml_A_profile-2026-06_mc":
        return VerifyResult("G1", False, {"reason": "wrong spec release recorded"}, measured)

    ret_ok = metrics.get("ret_test_word") == hex(RET_WORD) and metrics.get("ret_test_passed") is True
    measured["ret_test_passed"] = metrics.get("ret_test_passed")

    passed = tiling_ok and ret_ok
    return VerifyResult("G1", passed, evidence, measured)
