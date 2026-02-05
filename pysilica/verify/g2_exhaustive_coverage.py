from __future__ import annotations

from pathlib import Path

from pysilica.verify.types import VerifyResult

SHARD_COMPLETIONS = Path("artifacts/sweep/shards")
BITMAPS = Path("artifacts/bitmaps")
N_SHARDS = 256
SHARD_SIZE = 1 << 24


def verify_g2_exhaustive_coverage() -> VerifyResult:
    if not SHARD_COMPLETIONS.is_dir():
        return VerifyResult("G2", False, {"missing": str(SHARD_COMPLETIONS)}, {})

    completions = sorted(SHARD_COMPLETIONS.glob("*.json"))
    if len(completions) != N_SHARDS:
        return VerifyResult(
            "G2", False,
            {"shard_completion_files": len(completions), "expected": N_SHARDS},
            {},
        )

    if not BITMAPS.is_dir():
        return VerifyResult("G2", False, {"missing": str(BITMAPS)}, {})

    return VerifyResult(
        "G2", False,
        {"reason": "shard tiling / bitmap popcount / rehash checks not yet implemented"},
        {"shard_completion_files": len(completions)},
    )
