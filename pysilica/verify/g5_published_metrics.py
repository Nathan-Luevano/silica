from __future__ import annotations

import json
import mmap
from pathlib import Path

import numpy as np

from pysilica.verify.g4_disagreement_corpus import (
    CORPUS_DIR,
    _iter_shard_file_records,
    _shard_files,
)
from pysilica.verify.g4_disagreement_corpus import (
    METRICS_FILE as G4_METRICS_FILE,
)
from pysilica.verify.types import VerifyResult

REPORT = Path("artifacts/report/metrics.json")
BITMAPS_DIR = Path("artifacts/bitmaps")
TOTAL_BITS = 1 << 32
SHARD_BITS = 1 << 24
N_SHARDS = 256
TOOLS = ("capstone", "llvm", "unicorn")
TEXT_CATEGORIES = {"MNEMONIC", "OPERAND", "ALIAS", "FORMATTING", "NORMALIZATION_UNCERTAIN"}

_POPCOUNT_TABLE = np.array([i.bit_count() for i in range(256)], dtype=np.uint64)


def _popcount_xor(a: bytes, b: bytes) -> int:
    arr_a = np.frombuffer(a, dtype=np.uint8)
    arr_b = np.frombuffer(b, dtype=np.uint8)
    return int(_POPCOUNT_TABLE[np.bitwise_xor(arr_a, arr_b)].sum())


def _real_validity_disagreements(tool: str, spec_mm: mmap.mmap) -> tuple[int, float]:
    # micro is a straight XOR-popcount over the whole 512MiB bitmap; macro
    # re-slices it per-shard and averages those 256 rates unweighted, so a
    # concentrated failure mode in a few shards doesn't get diluted the way
    # a single aggregate rate would (docs/formats.md's rationale for this).
    path = BITMAPS_DIR / f"{tool}.bin"
    with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as tool_mm:
        total_disagree = _popcount_xor(tool_mm[:], spec_mm[:])
        shard_bytes = SHARD_BITS // 8
        shard_rates = []
        for i in range(N_SHARDS):
            lo, hi = i * shard_bytes, (i + 1) * shard_bytes
            disagree = _popcount_xor(tool_mm[lo:hi], spec_mm[lo:hi])
            shard_rates.append((shard_bytes * 8 - disagree) / (shard_bytes * 8))
    return total_disagree, sum(shard_rates) / len(shard_rates)


def _real_text_disagreements() -> dict[str, int]:
    # one pass over the corpus for all three tools at once, not one pass
    # per tool - the corpus is 835MB compressed and decompression, not
    # counting, is the expensive part, so streaming it three times over
    # was pure waste. still never materializes it (see the G4 OOM).
    counts = {tool: 0 for tool in TOOLS}
    for f in _shard_files():
        for r in _iter_shard_file_records(f):
            if r.get("category") not in TEXT_CATEGORIES:
                continue
            texts = r.get("oracle_text")
            if not isinstance(texts, dict):
                continue
            spec_text = texts.get("spec")
            for tool in TOOLS:
                if texts.get(tool) != spec_text:
                    counts[tool] += 1
    return counts


def _check_schema(report: dict[str, object]) -> dict[str, object] | None:
    if report.get("format_version") != 1:
        return {"reason": "missing/wrong format_version"}
    if report.get("total_words") != TOTAL_BITS:
        return {"reason": "total_words != 2**32", "actual": report.get("total_words")}
    per_tool = report.get("per_tool")
    if not isinstance(per_tool, dict) or set(per_tool.keys()) != set(TOOLS):
        return {"reason": "per_tool must have exactly capstone/llvm/unicorn", "actual": per_tool}
    ranking = report.get("tool_ranking_worst_first")
    if not isinstance(ranking, list) or sorted(ranking) != sorted(TOOLS):
        return {"reason": "tool_ranking_worst_first must list all three tools", "actual": ranking}
    for tool in TOOLS:
        t = per_tool[tool]
        required = (
            "validity_disagreements_with_spec",
            "validity_agreement_micro",
            "macro_validity_agreement",
            "text_tier_disagreements_with_spec",
            "text_tier_agreement_micro",
            "text_tier_method",
            "text_tier_sample_size",
            "text_tier_population",
        )
        missing = [k for k in required if k not in t]
        if missing:
            return {"reason": f"{tool} missing fields", "missing": missing}
    return None


def verify_g5_published_metrics() -> VerifyResult:
    if not REPORT.exists():
        return VerifyResult("G5", False, {"missing": str(REPORT)}, {})
    if not G4_METRICS_FILE.exists():
        return VerifyResult("G5", False, {"missing": str(G4_METRICS_FILE)}, {})
    if not CORPUS_DIR.is_dir() or not any(CORPUS_DIR.glob("*.zst")):
        return VerifyResult("G5", False, {"missing": str(CORPUS_DIR)}, {})
    for tool in (*TOOLS, "spec"):
        if not (BITMAPS_DIR / f"{tool}.bin").exists():
            return VerifyResult("G5", False, {"missing": str(BITMAPS_DIR / f'{tool}.bin')}, {})

    try:
        report = json.loads(REPORT.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return VerifyResult("G5", False, {"reason": f"unreadable report: {e}"}, {})

    schema_problem = _check_schema(report)
    if schema_problem:
        return VerifyResult("G5", False, schema_problem, {})

    g4_metrics = json.loads(G4_METRICS_FILE.read_text())

    spec_path = BITMAPS_DIR / "spec.bin"
    with spec_path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as spec_mm:
        spec_valid_count = int(_POPCOUNT_TABLE[np.frombuffer(spec_mm[:], dtype=np.uint8)].sum())
        if report.get("spec_valid_count") != spec_valid_count:
            return VerifyResult(
                "G5",
                False,
                {
                    "reason": "spec_valid_count doesn't match bitmap popcount",
                    "claimed": report.get("spec_valid_count"),
                    "actual": spec_valid_count,
                },
                {},
            )

        for tool in TOOLS:
            claimed = report["per_tool"][tool]
            for key in ("text_tier_method", "text_tier_sample_size", "text_tier_population"):
                if claimed.get(key) != g4_metrics.get(key):
                    return VerifyResult(
                        "G5",
                        False,
                        {
                            "reason": f"{tool}.{key} doesn't match g4_metrics.json",
                            "claimed": claimed.get(key),
                            "g4": g4_metrics.get(key),
                        },
                        {},
                    )

            real_disagree, real_macro = _real_validity_disagreements(tool, spec_mm)
            if claimed.get("validity_disagreements_with_spec") != real_disagree:
                return VerifyResult(
                    "G5",
                    False,
                    {
                        "reason": f"{tool} validity_disagreements_with_spec doesn't match bitmaps",
                        "claimed": claimed.get("validity_disagreements_with_spec"),
                        "actual": real_disagree,
                    },
                    {},
                )
            real_micro = (TOTAL_BITS - real_disagree) / TOTAL_BITS
            if abs(claimed.get("validity_agreement_micro", -1) - real_micro) > 1e-9:
                return VerifyResult(
                    "G5",
                    False,
                    {
                        "reason": f"{tool} validity_agreement_micro doesn't match recomputed value",
                        "claimed": claimed.get("validity_agreement_micro"),
                        "actual": real_micro,
                    },
                    {},
                )
            if abs(claimed.get("macro_validity_agreement", -1) - real_macro) > 1e-9:
                return VerifyResult(
                    "G5",
                    False,
                    {
                        "reason": f"{tool} macro_validity_agreement doesn't match recomputed per-shard average",
                        "claimed": claimed.get("macro_validity_agreement"),
                        "actual": real_macro,
                    },
                    {},
                )

    real_text_disagreements = _real_text_disagreements()
    for tool in TOOLS:
        claimed = report["per_tool"][tool]
        real_text_disagree = real_text_disagreements[tool]
        if claimed.get("text_tier_disagreements_with_spec") != real_text_disagree:
            return VerifyResult(
                "G5",
                False,
                {
                    "reason": f"{tool} text_tier_disagreements_with_spec doesn't match streamed corpus count",
                    "claimed": claimed.get("text_tier_disagreements_with_spec"),
                    "actual": real_text_disagree,
                },
                {},
            )
        population = claimed.get("text_tier_population", 0)
        real_text_micro = (population - real_text_disagree) / population if population else 0.0
        if abs(claimed.get("text_tier_agreement_micro", -1) - real_text_micro) > 1e-9:
            return VerifyResult(
                "G5",
                False,
                {
                    "reason": f"{tool} text_tier_agreement_micro doesn't match recomputed value",
                    "claimed": claimed.get("text_tier_agreement_micro"),
                    "actual": real_text_micro,
                },
                {},
            )

    real_ranking = sorted(TOOLS, key=lambda t: report["per_tool"][t]["validity_agreement_micro"])
    if report["tool_ranking_worst_first"] != real_ranking:
        return VerifyResult(
            "G5",
            False,
            {
                "reason": "tool_ranking_worst_first isn't sorted worst-first by its own numbers",
                "claimed": report["tool_ranking_worst_first"],
                "actual": real_ranking,
            },
            {},
        )

    return VerifyResult(
        "G5",
        True,
        {"report": str(REPORT)},
        {"tool_ranking_worst_first": report["tool_ranking_worst_first"], "spec_valid_count": spec_valid_count},
    )
