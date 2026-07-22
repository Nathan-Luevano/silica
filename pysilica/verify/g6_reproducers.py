from __future__ import annotations

import re
from pathlib import Path

from pysilica.verify.g4_disagreement_corpus import CORPUS_DIR, TAXONOMY, _iter_shard_file_records
from pysilica.verify.types import VerifyResult

REPRODUCERS = Path("artifacts/reproducers")
MIN_REPRODUCERS = 10
SHARD_BITS = 1 << 24
TOOLS = ("capstone", "llvm", "unicorn")

_FIELD_RE = re.compile(r"^-\s*(\w+):\s*(.+?)\s*$", re.MULTILINE)


def _parse_header(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _FIELD_RE.finditer(text)}


def _find_record(word: int) -> dict[str, object] | None:
    # exactly one shard file decompressed per lookup - cheap even for
    # hundreds of reproducers, never scans the whole corpus.
    shard_id = word // SHARD_BITS
    path = CORPUS_DIR / f"{shard_id:03d}.zst"
    if not path.exists():
        return None
    # corpus words are always zero-padded to 8 hex digits (see the real
    # records, e.g. "0x00000000") - plain hex() drops leading zero
    # nibbles and silently fails to match any word below 0x10000000.
    target_hex = f"0x{word:08x}"
    for r in _iter_shard_file_records(path):
        if str(r.get("word")) == target_hex:
            return r
    return None


def _oracle_str(record: dict[str, object], oracle: str) -> str:
    texts = record.get("oracle_text")
    if isinstance(texts, dict) and texts.get(oracle) is not None:
        return str(texts[oracle])
    valids = record.get("oracle_valid")
    if isinstance(valids, dict):
        return "valid" if valids.get(oracle) else "invalid"
    return ""


def _check_one(path: Path) -> dict[str, object] | None:
    try:
        text = path.read_text()
    except OSError as e:
        return {"file": str(path), "reason": f"unreadable: {e}"}

    fields = _parse_header(text)
    required = ("word", "category", "tool", "spec", "actual")
    missing = [f for f in required if f not in fields or not fields[f]]
    if missing:
        return {"file": str(path), "reason": "missing/empty header fields", "missing": missing}

    if fields["tool"] not in TOOLS:
        return {"file": str(path), "reason": "tool must be capstone/llvm/unicorn", "actual": fields["tool"]}
    if fields["category"] not in TAXONOMY:
        return {"file": str(path), "reason": "category not in taxonomy", "actual": fields["category"]}
    if fields["spec"] == fields["actual"]:
        return {"file": str(path), "reason": "spec and actual are identical - not a disagreement"}

    if not fields["word"].startswith("0x"):
        return {"file": str(path), "reason": "word not 0x-prefixed hex", "actual": fields["word"]}
    try:
        word = int(fields["word"], 16)
    except ValueError:
        return {"file": str(path), "reason": "word not valid hex", "actual": fields["word"]}

    record = _find_record(word)
    if record is None:
        return {"file": str(path), "reason": "word has no real disagreement record in the corpus", "word": fields["word"]}

    if record.get("category") != fields["category"]:
        return {
            "file": str(path),
            "reason": "category doesn't match the real corpus record",
            "claimed": fields["category"],
            "actual": record.get("category"),
        }

    real_spec = _oracle_str(record, "spec")
    real_actual = _oracle_str(record, fields["tool"])
    if fields["spec"] != real_spec:
        return {"file": str(path), "reason": "spec field doesn't match the real corpus record", "claimed": fields["spec"], "actual": real_spec}
    if fields["actual"] != real_actual:
        return {"file": str(path), "reason": "actual field doesn't match the real corpus record", "claimed": fields["actual"], "actual": real_actual}

    return None


def verify_g6_reproducers() -> VerifyResult:
    if not REPRODUCERS.is_dir():
        return VerifyResult("G6", False, {"missing": str(REPRODUCERS)}, {})

    files = sorted(REPRODUCERS.glob("*.md"))
    if len(files) < MIN_REPRODUCERS:
        return VerifyResult("G6", False, {"reproducer_files": len(files), "required": MIN_REPRODUCERS}, {})

    words_seen: dict[str, str] = {}
    for f in files:
        problem = _check_one(f)
        if problem:
            return VerifyResult("G6", False, problem, {})

        text = f.read_text()
        word = _parse_header(text)["word"]
        if word in words_seen:
            return VerifyResult(
                "G6",
                False,
                {"reason": "duplicate word across reproducers", "word": word, "files": [words_seen[word], str(f)]},
                {},
            )
        words_seen[word] = str(f)

    return VerifyResult(
        "G6",
        True,
        {"reproducer_files": [str(p) for p in files]},
        {"count": len(files), "required": MIN_REPRODUCERS, "distinct_words": len(words_seen)},
    )
