from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import zstandard

from pysilica.analyze.normalize import Normalizer, classify_disagreement

CORPUS_DIR = Path("artifacts/disagreements")
METRICS_FILE = Path("artifacts/g4_metrics.json")
ORACLES = ("capstone", "llvm", "spec", "unicorn")

# among classify_disagreement's possible outputs (EQUIVALENT, OPERAND,
# NORMALIZATION_UNCERTAIN, VALIDITY-for-missing-text), pick the least
# confident one across every oracle pair as the record's category -
# same "when unsure, don't collapse" conservatism as normalize.py itself
# (DESIGN-FINAL.md §7.2). MNEMONIC/ALIAS/FORMATTING aren't produced by
# classify_disagreement as it exists today, so they never appear here -
# not reinventing that logic, just using it as-is (see WORKLOG.md).
_PRIORITY = {"NORMALIZATION_UNCERTAIN": 3, "VALIDITY": 2, "OPERAND": 1, "EQUIVALENT": 0}

# unicorn's Oracle::disassemble() always returns the literal "<valid>"
# placeholder, never real disassembly text - it's execution-only, not a
# disassembler (crates/silica-oracles/src/unicorn.rs). Comparing it
# text-wise against capstone/llvm/spec would make every single word look
# like a disagreement for a reason that has nothing to do with the taxonomy
# this is trying to measure, so it's recorded in oracle_text but excluded
# from the pairwise comparisons that decide category.
TEXT_COMPARABLE_ORACLES = ("capstone", "llvm", "spec")


def classify_tier2_record(oracle_text: dict[str, str | None], normalizer: Normalizer) -> str | None:
    normed = {}
    for o in TEXT_COMPARABLE_ORACLES:
        raw = oracle_text.get(o)
        normed[o] = normalizer.normalize(raw).normalized if raw is not None else None

    worst = "EQUIVALENT"
    for i, a in enumerate(TEXT_COMPARABLE_ORACLES):
        for b in TEXT_COMPARABLE_ORACLES[i + 1 :]:
            cat = classify_disagreement(oracle_text.get(a), oracle_text.get(b), normed[a], normed[b])
            if _PRIORITY[cat] > _PRIORITY[worst]:
                worst = cat
    return None if worst == "EQUIVALENT" else worst


def build_tier2_records(disasm_jsonl: Path, normalizer: Normalizer) -> tuple[dict[int, list[str]], Counter[str]]:
    by_shard: dict[int, list[str]] = defaultdict(list)
    counts: Counter[str] = Counter()
    with disasm_jsonl.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            word = int(rec["word"], 16)
            oracle_text = rec["oracle_text"]
            category = classify_tier2_record(oracle_text, normalizer)
            if category is None:
                continue
            oracle_valid = {o: oracle_text.get(o) is not None for o in ORACLES}
            out = {
                "format_version": 1,
                "word": rec["word"],
                "category": category,
                "oracle_valid": oracle_valid,
                "oracle_text": oracle_text,
            }
            by_shard[word >> 24].append(json.dumps(out))
            counts[category] += 1
    return by_shard, counts


def merge_and_compress(
    tier1_dir: Path,
    tier2_by_shard: dict[int, list[str]],
    out_dir: Path,
) -> int:
    # tier1 record counts come from silica-sweep g4-tier1's own stdout
    # (authoritative, already printed while writing these same files) -
    # deliberately not re-reading 141GB of jsonl a second time just to
    # count lines it already told us the count of.
    out_dir.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor()
    shards_with_disagreements = 0

    all_shard_ids = set(tier2_by_shard.keys())
    for p in tier1_dir.glob("*.jsonl"):
        all_shard_ids.add(int(p.stem))

    for shard_id in sorted(all_shard_ids):
        tier1_path = tier1_dir / f"{shard_id:03d}.jsonl"
        tier2_lines = tier2_by_shard.get(shard_id, [])
        if not tier1_path.exists() and not tier2_lines:
            continue
        shards_with_disagreements += 1
        out_path = out_dir / f"{shard_id:03d}.zst"
        with out_path.open("wb") as out_f, cctx.stream_writer(out_f) as writer:
            if tier1_path.exists():
                with tier1_path.open("rb") as tier1_f:
                    shutil.copyfileobj(tier1_f, writer)
            for line in tier2_lines:
                writer.write(line.encode("utf-8"))
                writer.write(b"\n")

    return shards_with_disagreements
