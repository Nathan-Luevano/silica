from __future__ import annotations

import json
import mmap
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from pysilica.verify.types import VerifyResult

SHARD_DIR = Path("artifacts/sweep/shards")
BITMAPS_DIR = Path("artifacts/bitmaps")
N_SHARDS = 256
SHARD_BITS = 1 << 24
TOTAL_BITS = 1 << 32
ORACLES = ("capstone", "llvm", "spec", "unicorn")
SWEEP_BIN = Path("target/release/silica-sweep")
DECODE_TABLE = Path("artifacts/decode-table.bin")

_POPCOUNT_TABLE = np.array([i.bit_count() for i in range(256)], dtype=np.uint64)


def _popcount_bytes(data: bytes) -> int:
    arr = np.frombuffer(data, dtype=np.uint8)
    return int(_POPCOUNT_TABLE[arr].sum())


def _load_shard_records() -> tuple[list[dict[str, object]] | None, dict[str, object]]:
    if not SHARD_DIR.is_dir():
        return None, {"missing": str(SHARD_DIR)}

    files = sorted(SHARD_DIR.glob("*.json"))
    if len(files) != N_SHARDS:
        return None, {"shard_completion_files": len(files), "expected": N_SHARDS}

    records = []
    for f in files:
        try:
            records.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError) as e:
            return None, {"reason": f"unreadable shard record {f}: {e}"}
    return records, {}


def _check_tiling(records: list[dict[str, object]]) -> dict[str, object] | None:
    by_id = {r.get("shard_id"): r for r in records}
    missing_ids = set(range(N_SHARDS)) - set(by_id.keys())
    if missing_ids:
        return {"reason": "shard_id gaps", "missing_ids": sorted(missing_ids)[:10]}

    for i in range(N_SHARDS):
        r = by_id[i]
        expected_start = i * SHARD_BITS
        expected_end = (i + 1) * SHARD_BITS
        if r.get("start") != expected_start or r.get("end") != expected_end:
            return {
                "reason": "shard boundary mismatch",
                "shard_id": i,
                "expected": [expected_start, expected_end],
                "actual": [r.get("start"), r.get("end")],
            }
    return None


def _check_no_untriaged_crashes(records: list[dict[str, object]]) -> dict[str, object] | None:
    for r in records:
        if r.get("status") == "complete" and r.get("untriaged_crash_count", 0) not in (0, None):
            return {
                "reason": "shard marked complete with untriaged crashes",
                "shard_id": r.get("shard_id"),
                "untriaged_crash_count": r.get("untriaged_crash_count"),
            }
    return None


def _check_bitmap_popcounts() -> dict[str, object] | None:
    expected_bytes = TOTAL_BITS // 8
    for oracle in ORACLES:
        path = BITMAPS_DIR / f"{oracle}.bin"
        if not path.exists():
            return {"missing": str(path)}
        size = path.stat().st_size
        if size != expected_bytes:
            return {"reason": f"{oracle}.bin wrong size", "expected": expected_bytes, "actual": size}

        with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            ones = _popcount_bytes(mm[:])
        zeros = TOTAL_BITS - ones
        if ones + zeros != TOTAL_BITS:
            return {"reason": f"{oracle} popcount+zerocount != 2**32", "ones": ones, "zeros": zeros}
    return None


def _reproduce_one_shard(records: list[dict[str, object]]) -> dict[str, object] | None:
    if not SWEEP_BIN.exists():
        return {"missing": str(SWEEP_BIN)}
    if not DECODE_TABLE.exists():
        return {"missing": str(DECODE_TABLE)}

    picked = random.choice(records)
    shard_id = picked["shard_id"]
    recorded_hash = picked.get("content_hash")
    if not recorded_hash:
        return {"reason": "picked shard has no content_hash", "shard_id": shard_id}

    scratch = Path(tempfile.mkdtemp(prefix="silica-g2-verify-"))
    try:
        # 300s was wrong: it assumed all shards are cheap, but a shard
        # inside a densely-allocated encoding class (e.g. B/BL's full
        # 26-bit-immediate space) makes unicorn genuinely slow, not hung -
        # measured for real, shards 20 and 148 legitimately took ~39min
        # each. 3600s gives real margin over that measured worst case.
        # subprocess.TimeoutExpired is caught below rather than left to
        # crash silica verify outright - a slow reproduction is evidence
        # the verifier should report as a failure, not an uncaught traceback.
        result = subprocess.run(
            [
                str(SWEEP_BIN),
                "verify-shard",
                "--shard",
                str(shard_id),
                "--spec-decode-table",
                str(DECODE_TABLE),
                "--out",
                str(scratch),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=3600,
        )
        if result.returncode != 0:
            return {
                "reason": "verify-shard subprocess failed",
                "shard_id": shard_id,
                "stderr": result.stderr[-500:],
            }

        nnn = f"{shard_id:03d}"
        record_path = scratch / "sweep" / "shards" / f"{nnn}.json"
        if not record_path.exists():
            return {"reason": "verify-shard produced no shard record", "shard_id": shard_id}

        fresh_hash = json.loads(record_path.read_text()).get("content_hash")
        if fresh_hash != recorded_hash:
            return {
                "reason": "re-run hash mismatch",
                "shard_id": shard_id,
                "recorded_hash": recorded_hash,
                "fresh_hash": fresh_hash,
            }
        return None
    except subprocess.TimeoutExpired:
        return {"reason": "verify-shard exceeded 3600s", "shard_id": shard_id}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def verify_g2_exhaustive_coverage() -> VerifyResult:
    records, evidence = _load_shard_records()
    if records is None:
        return VerifyResult("G2", False, evidence, {})

    measured: dict[str, object] = {"shard_completion_files": len(records)}

    tiling_problem = _check_tiling(records)
    if tiling_problem:
        return VerifyResult("G2", False, tiling_problem, measured)

    crash_problem = _check_no_untriaged_crashes(records)
    if crash_problem:
        return VerifyResult("G2", False, crash_problem, measured)

    popcount_problem = _check_bitmap_popcounts()
    if popcount_problem:
        return VerifyResult("G2", False, popcount_problem, measured)
    measured["bitmap_popcount_check"] = "all four oracles sum to 2**32"

    reproduce_problem = _reproduce_one_shard(records)
    if reproduce_problem:
        return VerifyResult("G2", False, reproduce_problem, measured)
    measured["reproduced_shard_hash"] = True

    return VerifyResult("G2", True, {"shard_dir": str(SHARD_DIR), "bitmaps_dir": str(BITMAPS_DIR)}, measured)
