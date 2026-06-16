from __future__ import annotations

import contextlib
import json
import mmap
import random
from pathlib import Path

import zstandard

from pysilica.verify.types import VerifyResult

CORPUS_DIR = Path("artifacts/disagreements")
METRICS_FILE = Path("artifacts/g4_metrics.json")
BITMAPS_DIR = Path("artifacts/bitmaps")
TOTAL_BITS = 1 << 32
ORACLES = ("capstone", "llvm", "spec", "unicorn")
TAXONOMY = {"VALIDITY", "MNEMONIC", "OPERAND", "ALIAS", "FORMATTING", "NORMALIZATION_UNCERTAIN", "CRASH"}
SAMPLE_SIZE = 20000


def _read_corpus_records() -> list[dict[str, object]] | dict[str, object]:
    dctx = zstandard.ZstdDecompressor()
    records: list[dict[str, object]] = []
    for f in sorted(CORPUS_DIR.glob("*.zst")):
        try:
            with f.open("rb") as fh, dctx.stream_reader(fh) as reader:
                text = reader.read().decode("utf-8")
        except (OSError, zstandard.ZstdError) as e:
            return {"reason": f"unreadable corpus file {f}: {e}"}
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                return {"reason": f"malformed record in {f}: {e}"}
    return records


def _check_record_schema(records: list[dict[str, object]]) -> dict[str, object] | None:
    for r in records:
        if r.get("format_version") != 1:
            return {"reason": "record missing/wrong format_version", "record": r}
        category = r.get("category")
        if category not in TAXONOMY:
            return {"reason": "record category not in taxonomy", "category": category}
        oracle_valid = r.get("oracle_valid")
        if not isinstance(oracle_valid, dict) or set(oracle_valid.keys()) != set(ORACLES):
            return {"reason": "record oracle_valid missing an oracle", "record": r}
        word = r.get("word")
        if not isinstance(word, str) or not word.startswith("0x"):
            return {"reason": "record word not a 0x-prefixed hex string", "record": r}
    return None


def _bitmap_bit(mmaps: dict[str, mmap.mmap], oracle: str, word: int) -> bool:
    byte = mmaps[oracle][word // 8]
    return bool((byte >> (word % 8)) & 1)


def _check_validity_tier_against_bitmaps(records: list[dict[str, object]]) -> dict[str, object] | None:
    # the real check: sample real words, compute true per-oracle validity
    # straight from the swept bitmaps, and confirm every genuine validity
    # disagreement in the sample has a corresponding corpus record - not
    # just that the corpus file parses, but that it actually reflects the
    # data it claims to summarize (docs/formats.md's "checkable against the
    # bitmaps independent of anything else" design intent).
    for oracle in ORACLES:
        path = BITMAPS_DIR / f"{oracle}.bin"
        if not path.exists():
            return {"missing": str(path)}

    word_to_record = {
        int(str(r["word"]), 16): r for r in records if r.get("category") == "VALIDITY"
    }

    with contextlib.ExitStack() as stack:
        files = [stack.enter_context(open(BITMAPS_DIR / f"{o}.bin", "rb")) for o in ORACLES]
        mmaps = {
            o: mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) for o, f in zip(ORACLES, files)
        }
        rng = random.Random(0xC0FFEE)
        missing_examples: list[str] = []
        checked = 0
        real_disagreements = 0
        for _ in range(SAMPLE_SIZE):
            w = rng.randrange(TOTAL_BITS)
            valid = {o: _bitmap_bit(mmaps, o, w) for o in ORACLES}
            checked += 1
            if len(set(valid.values())) > 1:
                real_disagreements += 1
                rec = word_to_record.get(w)
                if rec is None and len(missing_examples) < 10:
                    missing_examples.append(hex(w))
        for m in mmaps.values():
            m.close()
        if missing_examples:
            return {
                "reason": "real validity disagreements found in sample with no corpus record",
                "missing_examples": missing_examples,
                "sample_size": checked,
                "real_disagreements_in_sample": real_disagreements,
            }
    return None


def verify_g4_disagreement_corpus() -> VerifyResult:
    if not CORPUS_DIR.is_dir() or not any(CORPUS_DIR.glob("*.zst")):
        return VerifyResult("G4", False, {"missing": str(CORPUS_DIR)}, {})

    if not METRICS_FILE.exists():
        return VerifyResult("G4", False, {"missing": str(METRICS_FILE)}, {})

    metrics = json.loads(METRICS_FILE.read_text())
    required_keys = {
        "format_version",
        "shards_with_disagreements",
        "total_disagreements",
        "category_counts",
        "validity_tier_exhaustive",
        "validity_disagreements",
        "text_tier_method",
    }
    missing_keys = required_keys - metrics.keys()
    if missing_keys:
        return VerifyResult("G4", False, {"missing_metric_keys": sorted(missing_keys)}, {})

    if metrics["validity_tier_exhaustive"] is not True:
        return VerifyResult(
            "G4", False,
            {"reason": "validity_tier_exhaustive must be true - DESIGN-FINAL.md §14 risk #2 fallback requires the bitmap-derived VALIDITY tier to be exhaustive, not sampled"},
            {},
        )

    if metrics["text_tier_method"] not in ("exhaustive", "sampled"):
        return VerifyResult("G4", False, {"reason": "text_tier_method must be 'exhaustive' or 'sampled'"}, {})

    if metrics["text_tier_method"] == "sampled":
        for k in ("text_tier_sample_size", "text_tier_population"):
            if not isinstance(metrics.get(k), int) or metrics[k] <= 0:
                return VerifyResult("G4", False, {"reason": f"sampled text tier requires real {k}"}, {})

    records_or_err = _read_corpus_records()
    if isinstance(records_or_err, dict):
        return VerifyResult("G4", False, records_or_err, {})
    records = records_or_err

    if not records:
        return VerifyResult("G4", False, {"reason": "corpus files present but contain zero records"}, {})

    measured: dict[str, object] = {
        "total_disagreements": metrics["total_disagreements"],
        "category_counts": metrics["category_counts"],
        "text_tier_method": metrics["text_tier_method"],
        "records_read": len(records),
    }

    schema_problem = _check_record_schema(records)
    if schema_problem:
        return VerifyResult("G4", False, schema_problem, measured)

    unknown_categories = set(metrics["category_counts"].keys()) - TAXONOMY
    if unknown_categories:
        return VerifyResult(
            "G4", False,
            {"reason": "g4_metrics.json category_counts has non-taxonomy keys", "unknown": sorted(unknown_categories)},
            measured,
        )

    validity_problem = _check_validity_tier_against_bitmaps(records)
    if validity_problem:
        return VerifyResult("G4", False, validity_problem, measured)
    measured["validity_tier_cross_checked"] = SAMPLE_SIZE

    return VerifyResult(
        "G4", True,
        {"corpus_dir": str(CORPUS_DIR), "metrics_file": str(METRICS_FILE)},
        measured,
    )
