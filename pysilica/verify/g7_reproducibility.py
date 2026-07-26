from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from pysilica.verify.types import VerifyResult

RESULT_HASH = Path("artifacts/result_hash.txt")
ENV_FILE = Path("environment.yml")
MAKEFILE = Path("Makefile")
DECODE_TABLE = Path("artifacts/decode-table.bin")
BITMAPS_DIR = Path("artifacts/bitmaps")
G4_METRICS = Path("artifacts/g4_metrics.json")
G5_REPORT = Path("artifacts/report/metrics.json")
REPRODUCERS_DIR = Path("artifacts/reproducers")

PINNED_PACKAGES = ("capstone", "llvmdev", "llvm-tools", "unicorn", "rust")
_PINNED_RE = re.compile(r"^[\w-]+=\S+")

# what a one-command full pipeline must actually touch, in order -
# checked structurally against the Makefile target's recipe text, not
# by running it (a real re-run is G2's job, at shard granularity).
PIPELINE_MARKERS = (
    "compile-spec",
    "silica-sweep",
    "g4",
    "g5_report",
    "g6_reproducers",
    "result_hash",
)


def _check_pinned_tools() -> dict[str, object] | None:
    if not ENV_FILE.exists():
        return {"missing": str(ENV_FILE)}
    try:
        env = yaml.safe_load(ENV_FILE.read_text())
    except yaml.YAMLError as e:
        return {"reason": f"unreadable environment.yml: {e}"}

    deps = env.get("dependencies", []) if isinstance(env, dict) else []
    names_seen: dict[str, str] = {}
    for d in deps:
        if isinstance(d, str):
            name = d.split("=")[0].split(">")[0].split("<")[0].strip()
            names_seen[name] = d

    unpinned = []
    for pkg in PINNED_PACKAGES:
        entry = names_seen.get(pkg)
        if entry is None:
            unpinned.append(f"{pkg} (missing)")
        elif not _PINNED_RE.match(entry):
            unpinned.append(entry)
    if unpinned:
        return {"reason": "unpinned or missing tool versions in environment.yml", "unpinned": unpinned}
    return None


def _check_one_command() -> dict[str, object] | None:
    if not MAKEFILE.exists():
        return {"missing": str(MAKEFILE)}
    text = MAKEFILE.read_text()
    missing = [m for m in PIPELINE_MARKERS if m not in text]
    if missing:
        return {"reason": "no Makefile target chains the full pipeline", "missing_markers": missing}
    return None


def _compute_result_hash() -> str | None:
    for path in (DECODE_TABLE, G4_METRICS, G5_REPORT):
        if not path.exists():
            return None
    bitmap_paths = [BITMAPS_DIR / f"{o}.bin" for o in ("capstone", "llvm", "spec", "unicorn")]
    for p in bitmap_paths:
        if not p.exists():
            return None
    reproducer_files = sorted(REPRODUCERS_DIR.glob("*.md")) if REPRODUCERS_DIR.is_dir() else []

    h = hashlib.sha256()
    h.update(DECODE_TABLE.read_bytes())
    for p in bitmap_paths:
        h.update(p.read_bytes())
    h.update(G4_METRICS.read_bytes())
    h.update(G5_REPORT.read_bytes())
    for p in reproducer_files:
        h.update(p.read_bytes())
    return h.hexdigest()


def verify_g7_reproducibility() -> VerifyResult:
    pin_problem = _check_pinned_tools()
    if pin_problem:
        return VerifyResult("G7", False, pin_problem, {})

    command_problem = _check_one_command()
    if command_problem:
        return VerifyResult("G7", False, command_problem, {})

    if not RESULT_HASH.exists():
        return VerifyResult("G7", False, {"missing": str(RESULT_HASH)}, {})
    claimed = RESULT_HASH.read_text().strip()

    real = _compute_result_hash()
    if real is None:
        return VerifyResult("G7", False, {"reason": "not all inputs to the result hash exist yet"}, {})

    if claimed != real:
        return VerifyResult(
            "G7",
            False,
            {"reason": "result_hash.txt doesn't match a fresh recomputation", "claimed": claimed, "actual": real},
            {},
        )

    return VerifyResult(
        "G7",
        True,
        {"result_hash_file": str(RESULT_HASH)},
        {"result_hash": real},
    )
