from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def full_artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    _write(
        root / "report" / "metrics.json",
        json.dumps(
            {
                "format_version": 1,
                "total_words": 4294967296,
                "spec_valid_count": 1799435776,
                "per_tool": {
                    tool: {
                        "validity_disagreements_with_spec": disagreements,
                        "validity_agreement_micro": 1 - disagreements / 4294967296,
                        "macro_validity_agreement": 1 - disagreements / 4294967296,
                        "text_tier_disagreements_with_spec": 1000,
                        "text_tier_agreement_micro": 0.999,
                        "text_tier_method": "sampled",
                        "text_tier_sample_size": 1000000,
                        "text_tier_population": 1266064016,
                    }
                    for tool, disagreements in (
                        ("capstone", 653395392),
                        ("llvm", 530749558),
                        ("unicorn", 504171377),
                    )
                },
                "tool_ranking_worst_first": ["capstone", "llvm", "unicorn"],
            }
        ),
    )
    _write(
        root / "g1_metrics.json",
        json.dumps(
            {
                "spec_release": "ISA_A64_xml_A_profile-2026-06_mc",
                "allocated": 1799435776,
                "unallocated": 2495531520,
            }
        ),
    )
    _write(
        root / "g4_metrics.json",
        json.dumps(
            {
                "format_version": 1,
                "shards_with_disagreements": 1,
                "total_disagreements": 3,
                "category_counts": {"VALIDITY": 2, "OPERAND": 1},
                "validity_tier_exhaustive": True,
                "validity_disagreements": 2,
                "text_tier_method": "sampled",
                "text_tier_sample_size": 1000000,
                "text_tier_population": 1266064016,
            }
        ),
    )
    _write(root / "result_hash.txt", "a" * 64 + "\n")
    _write(
        root / "sweep" / "shards" / "000.json",
        json.dumps(
            {
                "shard_id": 0,
                "start": 0,
                "end": 16777216,
                "oracles": ["capstone", "llvm", "spec", "unicorn"],
                "valid_counts": {"capstone": 65536, "llvm": 65536, "spec": 65536, "unicorn": 0},
                "crash_count": 0,
                "untriaged_crash_count": 0,
                "content_hash": "d" * 64,
                "duration_ms": 63232,
                "status": "complete",
            }
        ),
    )
    _write(
        root / "reproducers" / "00-0x109b485a.md",
        "- word: 0x109b485a\n"
        "- category: OPERAND\n"
        "- tool: capstone\n"
        "- spec: ADR\n"
        "- actual: adr x26, #0xfffffffffff36908\n"
        "\n"
        "Some prose about the disagreement.\n",
    )
    _write(
        tmp_path / "GOALS.yml",
        "goals:\n"
        "  - id: G1\n"
        "    statement: Parse the ARM ISA XML\n"
        "    verifier: verify_g1_spec_oracle\n"
        "    verifier_file: pysilica/verify/g1_spec_oracle.py\n"
        "    verifier_sha256: " + "9" * 64 + "\n"
        "    status: pass\n",
    )

    import zstandard

    records = [
        {
            "format_version": 1,
            "word": "0x00000000",
            "category": "VALIDITY",
            "oracle_valid": {"capstone": True, "llvm": False, "spec": False, "unicorn": False},
            "oracle_text": {"capstone": None, "llvm": None, "spec": None, "unicorn": None},
        },
        {
            "format_version": 1,
            "word": "0x00000001",
            "category": "VALIDITY",
            "oracle_valid": {"capstone": True, "llvm": False, "spec": False, "unicorn": False},
            "oracle_text": {"capstone": None, "llvm": None, "spec": None, "unicorn": None},
        },
        {
            "format_version": 1,
            "word": "0x00000002",
            "category": "OPERAND",
            "oracle_valid": {"capstone": True, "llvm": True, "spec": True, "unicorn": True},
            "oracle_text": {
                "capstone": "adr x26, #0xfff",
                "llvm": "adr\tx26, #-825080",
                "spec": "ADR",
                "unicorn": "<valid>",
            },
        },
    ]
    # the real corpus mixes both spellings inside one file: the sweep writes
    # VALIDITY records compact, the text-tier pass writes them spaced. a
    # fixture that only does one hides an entire class of bug.
    payload = "".join(
        json.dumps(r, separators=(",", ":") if r["category"] == "VALIDITY" else (", ", ": "))
        + "\n"
        for r in records
    )
    blob = zstandard.ZstdCompressor().compress(payload.encode())
    (root / "disagreements").mkdir(parents=True, exist_ok=True)
    (root / "disagreements" / "000.zst").write_bytes(blob)
    return root


@pytest.fixture
def published_artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "pub" / "artifacts"
    _write(
        root / "reproducers" / "00-0x109b485a.md",
        "- word: 0x109b485a\n- category: OPERAND\n- tool: capstone\n- spec: ADR\n- actual: adr\n",
    )
    _write(root / "result_hash.txt", "b" * 64)
    return root


@pytest.fixture
def corrupt_artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "bad" / "artifacts"
    _write(root / "report" / "metrics.json", '{"format_version": 1, "per_tool": "not an object"}')
    _write(root / "g4_metrics.json", "{ not json")
    _write(root / "result_hash.txt", "deadbeef")
    _write(root / "sweep" / "shards" / "000.json", '{"shard_id": "not an int"}')
    _write(
        root / "reproducers" / "00-bad.md",
        "- word: 0xnothex\n- category: BOGUS\n- tool: gcc\n- spec: same\n- actual: same\n",
    )
    (root / "disagreements").mkdir(parents=True, exist_ok=True)
    (root / "disagreements" / "000.zst").write_bytes(b"this is not a zstd frame")
    return root
