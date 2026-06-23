from __future__ import annotations

import json

import zstandard

from pysilica.analyze.g4_corpus import (
    build_tier2_records,
    classify_tier2_record,
    merge_and_compress,
)
from pysilica.analyze.normalize import Normalizer


def test_equivalent_text_produces_no_record():
    norm = Normalizer()
    text = {"capstone": "ret", "llvm": "RET", "spec": "RET", "unicorn": "<valid>"}
    assert classify_tier2_record(text, norm) is None


def test_operand_mismatch_classified():
    norm = Normalizer()
    text = {"capstone": "mov w0, w1", "llvm": "mov w0, w2", "spec": "MOV W0, W1", "unicorn": "<valid>"}
    assert classify_tier2_record(text, norm) == "OPERAND"


def test_different_mnemonic_classified_normalization_uncertain():
    norm = Normalizer()
    text = {"capstone": "add w0, w1, w2", "llvm": "sub w0, w1, w2", "spec": "ADD W0, W1, W2", "unicorn": "<valid>"}
    assert classify_tier2_record(text, norm) == "NORMALIZATION_UNCERTAIN"


def test_unicorn_placeholder_text_never_drives_classification():
    # unicorn's disassemble() always returns the literal "<valid>" (it's
    # execution-only, see g4_disasm.rs) - if it were compared for real it
    # would make every single word look like a disagreement, so it must be
    # excluded from the pairwise category decision even though it's still
    # recorded in oracle_text
    norm = Normalizer()
    text = {"capstone": "ret", "llvm": "ret", "spec": "RET", "unicorn": "<valid>"}
    assert classify_tier2_record(text, norm) is None


def test_build_tier2_records_groups_by_shard(tmp_path):
    norm = Normalizer()
    disasm_file = tmp_path / "disasm.jsonl"
    word_shard1 = 0x01000001  # shard 1
    records = [
        {
            "word": f"0x{word_shard1:08x}",
            "oracle_text": {"capstone": "mov w0, w1", "llvm": "mov w0, w2", "spec": "MOV W0, W1", "unicorn": "<valid>"},
        }
    ]
    disasm_file.write_text("\n".join(json.dumps(r) for r in records))

    by_shard, counts = build_tier2_records(disasm_file, norm)
    assert set(by_shard.keys()) == {1}
    assert counts["OPERAND"] == 1


def test_merge_and_compress_round_trip(tmp_path):
    tier1_dir = tmp_path / "tier1"
    tier1_dir.mkdir()
    (tier1_dir / "000.jsonl").write_text(
        json.dumps({"format_version": 1, "word": "0x00000000", "category": "VALIDITY"}) + "\n"
    )
    tier2_by_shard = {
        0: [json.dumps({"format_version": 1, "word": "0x00000005", "category": "OPERAND"})],
        3: [json.dumps({"format_version": 1, "word": "0x03000000", "category": "OPERAND"})],
    }
    out_dir = tmp_path / "disagreements"
    shards_with_disagreements = merge_and_compress(tier1_dir, tier2_by_shard, out_dir)
    assert shards_with_disagreements == 2

    dctx = zstandard.ZstdDecompressor()
    with (out_dir / "000.zst").open("rb") as fh, dctx.stream_reader(fh) as reader:
        lines = reader.read().decode("utf-8").splitlines()
    assert len(lines) == 2

    with (out_dir / "003.zst").open("rb") as fh, dctx.stream_reader(fh) as reader:
        lines = reader.read().decode("utf-8").splitlines()
    assert len(lines) == 1
