from __future__ import annotations

import contextlib
import json
import mmap
import random
from collections.abc import Iterator
from pathlib import Path

import zstandard

from pysilica.verify.types import VerifyResult

CORPUS_DIR = Path("artifacts/disagreements")
METRICS_FILE = Path("artifacts/g4_metrics.json")
BITMAPS_DIR = Path("artifacts/bitmaps")
TOTAL_BITS = 1 << 32
SHARD_BITS = 1 << 24
ORACLES = ("capstone", "llvm", "spec", "unicorn")
TAXONOMY = {"VALIDITY", "MNEMONIC", "OPERAND", "ALIAS", "FORMATTING", "NORMALIZATION_UNCERTAIN", "CRASH"}
SAMPLE_SIZE = 20000

_DCTX = zstandard.ZstdDecompressor()


def _iter_shard_file_records(path: Path) -> Iterator[dict[str, object]]:
    # one shard file at a time, one line at a time - the real corpus has
    # 724M+ records (~449GB if ever materialized as a Python list, which is
    # exactly the OOM a P4 implementer hit trying to satisfy an earlier,
    # non-streaming version of this verifier). shard files are individually
    # bounded (at most 2**24 words' worth of disagreements), so streaming
    # one file at a time keeps memory bounded regardless of corpus size.
    with path.open("rb") as fh, _DCTX.stream_reader(fh) as reader:
        buf = b""
        while True:
            chunk = reader.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                if line.strip():
                    yield json.loads(line)
        if buf.strip():
            yield json.loads(buf)


def _shard_files() -> list[Path]:
    return sorted(CORPUS_DIR.glob("*.zst"))


def _check_schema_streaming() -> tuple[dict[str, object] | None, int]:
    total = 0
    for f in _shard_files():
        try:
            for r in _iter_shard_file_records(f):
                total += 1
                if r.get("format_version") != 1:
                    return {"reason": "record missing/wrong format_version", "file": str(f)}, total
                if r.get("category") not in TAXONOMY:
                    return {"reason": "record category not in taxonomy", "category": r.get("category"), "file": str(f)}, total
                oracle_valid = r.get("oracle_valid")
                if not isinstance(oracle_valid, dict) or set(oracle_valid.keys()) != set(ORACLES):
                    return {"reason": "record oracle_valid missing an oracle", "file": str(f)}, total
                word = r.get("word")
                if not isinstance(word, str) or not word.startswith("0x"):
                    return {"reason": "record word not a 0x-prefixed hex string", "file": str(f)}, total
        except (OSError, zstandard.ZstdError, json.JSONDecodeError) as e:
            return {"reason": f"unreadable/malformed corpus file {f}: {e}"}, total
    return None, total


def _bitmap_bit(mmaps: dict[str, mmap.mmap], oracle: str, word: int) -> bool:
    byte = mmaps[oracle][word // 8]
    return bool((byte >> (word % 8)) & 1)


def _shard_validity_words(shard_id: int, cache: dict[int, set[int]]) -> set[int]:
    if shard_id in cache:
        return cache[shard_id]
    path = CORPUS_DIR / f"{shard_id:03d}.zst"
    words: set[int] = set()
    if path.exists():
        for r in _iter_shard_file_records(path):
            if r.get("category") == "VALIDITY":
                words.add(int(str(r["word"]), 16))
    cache[shard_id] = words
    return words


def _check_validity_tier_against_bitmaps() -> dict[str, object] | None:
    # the real check: sample real words, compute true per-oracle validity
    # straight from the swept bitmaps, and confirm every genuine validity
    # disagreement in the sample has a corresponding corpus record - not
    # just that the corpus file parses, but that it actually reflects the
    # data it claims to summarize. only the specific shard file(s) a sample
    # actually lands in get decompressed (at most SAMPLE_SIZE distinct
    # shards, capped at 256 total anyway), never the whole corpus.
    for oracle in ORACLES:
        path = BITMAPS_DIR / f"{oracle}.bin"
        if not path.exists():
            return {"missing": str(path)}

    shard_cache: dict[int, set[int]] = {}

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
                shard_id = w // SHARD_BITS
                if w not in _shard_validity_words(shard_id, shard_cache) and len(missing_examples) < 10:
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

    unknown_categories = set(metrics["category_counts"].keys()) - TAXONOMY
    if unknown_categories:
        return VerifyResult(
            "G4", False,
            {"reason": "g4_metrics.json category_counts has non-taxonomy keys", "unknown": sorted(unknown_categories)},
            {},
        )

    schema_problem, records_read = _check_schema_streaming()
    measured: dict[str, object] = {
        "total_disagreements": metrics["total_disagreements"],
        "category_counts": metrics["category_counts"],
        "text_tier_method": metrics["text_tier_method"],
        "records_read": records_read,
    }
    if schema_problem:
        return VerifyResult("G4", False, schema_problem, measured)

    if records_read == 0:
        return VerifyResult("G4", False, {"reason": "corpus files present but contain zero records"}, measured)

    if records_read != metrics["total_disagreements"]:
        return VerifyResult(
            "G4", False,
            {"reason": "records_read != g4_metrics.json total_disagreements", "records_read": records_read, "claimed": metrics["total_disagreements"]},
            measured,
        )

    validity_problem = _check_validity_tier_against_bitmaps()
    if validity_problem:
        return VerifyResult("G4", False, validity_problem, measured)
    measured["validity_tier_cross_checked"] = SAMPLE_SIZE

    return VerifyResult(
        "G4", True,
        {"corpus_dir": str(CORPUS_DIR), "metrics_file": str(METRICS_FILE)},
        measured,
    )
