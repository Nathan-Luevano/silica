from __future__ import annotations

import json
import mmap
from pathlib import Path

import numpy as np

from pysilica.verify.g4_disagreement_corpus import (
    METRICS_FILE as G4_METRICS_FILE,
)
from pysilica.verify.g4_disagreement_corpus import (
    _iter_shard_file_records,
    _shard_files,
)

REPORT = Path("artifacts/report/metrics.json")
BITMAPS_DIR = Path("artifacts/bitmaps")
TOTAL_WORDS = 1 << 32
SHARD_BITS = 1 << 24
N_SHARDS = 256
TOOLS = ("capstone", "llvm", "unicorn")
TEXT_CATEGORIES = {"MNEMONIC", "OPERAND", "ALIAS", "FORMATTING", "NORMALIZATION_UNCERTAIN"}

_POPCOUNT_TABLE = np.array([i.bit_count() for i in range(256)], dtype=np.uint64)


def _popcount_bytes(b: bytes | memoryview) -> int:
    return int(_POPCOUNT_TABLE[np.frombuffer(b, dtype=np.uint8)].sum())


def _popcount_xor(a: bytes | memoryview, b: bytes | memoryview) -> int:
    arr_a = np.frombuffer(a, dtype=np.uint8)
    arr_b = np.frombuffer(b, dtype=np.uint8)
    return int(_POPCOUNT_TABLE[np.bitwise_xor(arr_a, arr_b)].sum())


def _validity_stats(tool: str, spec_mm: mmap.mmap) -> tuple[int, float, float]:
    # micro: one XOR-popcount over the whole 512MiB bitmap. macro: same
    # thing sliced into 256 shards, averaged unweighted, so a concentrated
    # failure mode in a few shards isn't diluted the way micro would.
    path = BITMAPS_DIR / f"{tool}.bin"
    with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as tool_mm:
        disagree = _popcount_xor(tool_mm[:], spec_mm[:])
        shard_bytes = SHARD_BITS // 8
        shard_rates = []
        for i in range(N_SHARDS):
            lo, hi = i * shard_bytes, (i + 1) * shard_bytes
            shard_disagree = _popcount_xor(tool_mm[lo:hi], spec_mm[lo:hi])
            shard_rates.append((SHARD_BITS - shard_disagree) / SHARD_BITS)
    micro = (TOTAL_WORDS - disagree) / TOTAL_WORDS
    macro = sum(shard_rates) / len(shard_rates)
    return disagree, micro, macro


def _text_tier_disagreements() -> dict[str, int]:
    # streams the corpus once, one shard file at a time, one line at a
    # time - never materializes it (see the G4 OOM incident, WORKLOG.md
    # ~07ac606). counts all three tools in the same pass.
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


def main() -> None:
    g4_metrics = json.loads(G4_METRICS_FILE.read_text())
    text_tier_method = g4_metrics["text_tier_method"]
    text_tier_sample_size = g4_metrics["text_tier_sample_size"]
    text_tier_population = g4_metrics["text_tier_population"]

    spec_path = BITMAPS_DIR / "spec.bin"
    with spec_path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as spec_mm:
        spec_valid_count = _popcount_bytes(spec_mm[:])
        validity = {tool: _validity_stats(tool, spec_mm) for tool in TOOLS}

    text_disagreements = _text_tier_disagreements()

    per_tool = {}
    for tool in TOOLS:
        disagree, micro, macro = validity[tool]
        text_disagree = text_disagreements[tool]
        text_micro = (text_tier_population - text_disagree) / text_tier_population
        per_tool[tool] = {
            "validity_disagreements_with_spec": disagree,
            "validity_agreement_micro": micro,
            "macro_validity_agreement": macro,
            "text_tier_disagreements_with_spec": text_disagree,
            "text_tier_agreement_micro": text_micro,
            "text_tier_method": text_tier_method,
            "text_tier_sample_size": text_tier_sample_size,
            "text_tier_population": text_tier_population,
        }

    ranking = sorted(TOOLS, key=lambda t: per_tool[t]["validity_agreement_micro"])

    report = {
        "format_version": 1,
        "total_words": TOTAL_WORDS,
        "spec_valid_count": spec_valid_count,
        "per_tool": per_tool,
        "tool_ranking_worst_first": ranking,
    }

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
