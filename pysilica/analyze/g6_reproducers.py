from __future__ import annotations

from pathlib import Path

from pysilica.verify.g4_disagreement_corpus import (
    _iter_shard_file_records,
    _shard_files,
)
from pysilica.verify.g6_reproducers import TOOLS, _oracle_str

REPRODUCERS = Path("artifacts/reproducers")
MIN_REPRODUCERS = 10
# non-VALIDITY categories tell a more specific human-readable story than a
# bare valid/invalid flip, so prefer them; VALIDITY is the fallback.
PREFERRED_CATEGORIES = ("OPERAND", "MNEMONIC", "ALIAS", "FORMATTING", "NORMALIZATION_UNCERTAIN")
SHARDS_TO_SCAN = 5
# the verifier's _find_record() (read-only, see pysilica/verify/g6_reproducers.py)
# looks records up via hex(word), which drops leading zero nibbles - it
# won't match the corpus's zero-padded "0x0..." strings for any word below
# 0x10000000 (shards 0-15). staying at shard_id >= 16 sidesteps that
# entirely rather than picking words the verifier can never confirm.
MIN_SHARD_ID = 16
# cap per (category, spec-text) bucket - the corpus has millions of VALIDITY
# records and near-duplicate systematic families (e.g. every register
# permutation of one instruction); storing more than a handful of each
# would repeat the OOM this project already hit once (WORKLOG ~07ac606)
# without adding any diversity.
PER_KEY_CAP = 2
TOTAL_POOL_CAP = 200


def _candidate_tool(record: dict[str, object]) -> str | None:
    # pick whichever named tool actually disagrees with spec on this
    # record - a record can have e.g. capstone wrong but llvm right.
    spec_str = _oracle_str(record, "spec")
    for tool in TOOLS:
        if _oracle_str(record, tool) != spec_str:
            return tool
    return None


def _collect_candidates() -> list[tuple[dict[str, object], str]]:
    preferred: list[tuple[dict[str, object], str]] = []
    fallback: list[tuple[dict[str, object], str]] = []
    seen_words: set[str] = set()
    key_counts: dict[tuple[str, str], int] = {}

    shard_files = [p for p in _shard_files() if int(p.stem) >= MIN_SHARD_ID][:SHARDS_TO_SCAN]
    for path in shard_files:
        if len(preferred) + len(fallback) >= TOTAL_POOL_CAP:
            break
        for record in _iter_shard_file_records(path):
            word = str(record.get("word"))
            if word in seen_words:
                continue
            category = str(record.get("category"))
            tool = _candidate_tool(record)
            if tool is None:
                continue

            # dedupe key: same category + same spec text is the same
            # story told twice (e.g. every "and w.., w.., w.." OPERAND
            # variant) - cap how many of those we keep so a handful of
            # systematic families don't crowd out real diversity.
            key = (category, _oracle_str(record, "spec"))
            if key_counts.get(key, 0) >= PER_KEY_CAP:
                continue

            seen_words.add(word)
            key_counts[key] = key_counts.get(key, 0) + 1
            bucket = preferred if category in PREFERRED_CATEGORIES else fallback
            bucket.append((record, tool))

            if len(preferred) + len(fallback) >= TOTAL_POOL_CAP:
                break

    return preferred + fallback


def _render(record: dict[str, object], tool: str) -> str:
    word = str(record["word"])
    category = str(record["category"])
    spec_str = _oracle_str(record, "spec")
    actual_str = _oracle_str(record, tool)

    return (
        f"- word: {word}\n"
        f"- category: {category}\n"
        f"- tool: {tool}\n"
        f"- spec: {spec_str}\n"
        f"- actual: {actual_str}\n"
        "\n"
        f"Disassembling word `{word}` (category `{category}`), the spec oracle "
        f"reports `{spec_str}` while `{tool}` reports `{actual_str}`. Filing this "
        f"upstream against `{tool}` should reproduce with just this one 32-bit "
        "AArch64 encoding - no larger context needed.\n"
    )


def main() -> None:
    candidates = _collect_candidates()
    if len(candidates) < MIN_REPRODUCERS:
        raise SystemExit(
            f"only found {len(candidates)} candidate disagreements across the first "
            f"{SHARDS_TO_SCAN} shard files, need at least {MIN_REPRODUCERS} - scan more shards"
        )

    REPRODUCERS.mkdir(parents=True, exist_ok=True)
    chosen = candidates[:MIN_REPRODUCERS]
    for i, (record, tool) in enumerate(chosen):
        word = str(record["word"])
        out_path = REPRODUCERS / f"{i:02d}-{word}.md"
        out_path.write_text(_render(record, tool))

    print(f"wrote {len(chosen)} reproducers to {REPRODUCERS}")


if __name__ == "__main__":
    main()
