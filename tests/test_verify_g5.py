from __future__ import annotations

import json
import os

import zstandard

from pysilica.verify import g5_published_metrics as g5mod
from pysilica.verify.g5_published_metrics import verify_g5_published_metrics

# small fixture: 256 words, 16 shards of 16 words each - divides evenly,
# mirrors the real 2**32 / 256-shard layout at a testable scale.
SMALL_BITS = 1 << 8
SMALL_SHARD_BITS = 1 << 4
SMALL_N_SHARDS = SMALL_BITS // SMALL_SHARD_BITS


def _write_bitmap(path, bits_set: set[int]) -> None:
    data = bytearray(SMALL_BITS // 8)
    for w in bits_set:
        data[w // 8] |= 1 << (w % 8)
    path.write_bytes(bytes(data))


def _write_corpus(dir_, records: list[dict]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor()
    body = "\n".join(json.dumps(r) for r in records).encode("utf-8")
    (dir_ / "000.zst").write_bytes(cctx.compress(body))


def _write_g4_metrics(path, **overrides) -> None:
    base = {
        "format_version": 1,
        "text_tier_method": "exhaustive",
        "text_tier_sample_size": 100,
        "text_tier_population": 100,
    }
    base.update(overrides)
    path.write_text(json.dumps(base))


def _setup(tmp_path, monkeypatch, bitmaps: dict[str, set[int]]):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(g5mod, "TOTAL_BITS", SMALL_BITS)
    monkeypatch.setattr(g5mod, "SHARD_BITS", SMALL_SHARD_BITS)
    monkeypatch.setattr(g5mod, "N_SHARDS", SMALL_N_SHARDS)
    os.makedirs(tmp_path / "artifacts" / "bitmaps")
    for oracle, bits in bitmaps.items():
        _write_bitmap(tmp_path / "artifacts" / "bitmaps" / f"{oracle}.bin", bits)


def _base_per_tool(disagree: int, population: int, text_disagree: int) -> dict:
    micro = (SMALL_BITS - disagree) / SMALL_BITS
    text_micro = (population - text_disagree) / population if population else 0.0
    return {
        "validity_disagreements_with_spec": disagree,
        "validity_agreement_micro": micro,
        "macro_validity_agreement": micro,  # only spec bit 5 differs, so per-shard == overall here
        "text_tier_disagreements_with_spec": text_disagree,
        "text_tier_agreement_micro": text_micro,
        "text_tier_method": "exhaustive",
        "text_tier_sample_size": 100,
        "text_tier_population": population,
    }


def test_fails_closed_with_no_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = verify_g5_published_metrics()
    assert result.passed is False


def test_fails_when_validity_disagreements_dont_match_bitmaps(tmp_path, monkeypatch):
    # deliberately broken fixture: spec has bit 5 set, capstone doesn't -
    # that's 1 real disagreement, but the report claims 0.
    _setup(
        tmp_path,
        monkeypatch,
        {"capstone": set(), "llvm": {5}, "spec": {5}, "unicorn": {5}},
    )
    _write_corpus(tmp_path / "artifacts" / "disagreements", [])
    _write_g4_metrics(tmp_path / "artifacts" / "g4_metrics.json")
    report = {
        "format_version": 1,
        "total_words": SMALL_BITS,
        "spec_valid_count": 1,
        "per_tool": {
            "capstone": _base_per_tool(0, 100, 0),  # wrong: real is 1
            "llvm": _base_per_tool(0, 100, 0),
            "unicorn": _base_per_tool(0, 100, 0),
        },
        "tool_ranking_worst_first": ["capstone", "llvm", "unicorn"],
    }
    (tmp_path / "artifacts" / "report").mkdir(parents=True)
    (tmp_path / "artifacts" / "report" / "metrics.json").write_text(json.dumps(report))
    result = verify_g5_published_metrics()
    assert result.passed is False


def test_fails_when_ranking_not_sorted_worst_first(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        {"capstone": {5}, "llvm": set(), "spec": set(), "unicorn": set()},
    )
    _write_corpus(tmp_path / "artifacts" / "disagreements", [])
    _write_g4_metrics(tmp_path / "artifacts" / "g4_metrics.json")
    report = {
        "format_version": 1,
        "total_words": SMALL_BITS,
        "spec_valid_count": 0,
        "per_tool": {
            "capstone": _base_per_tool(1, 100, 0),
            "llvm": _base_per_tool(0, 100, 0),
            "unicorn": _base_per_tool(0, 100, 0),
        },
        # capstone has the only disagreement (worst) but is listed last
        "tool_ranking_worst_first": ["llvm", "unicorn", "capstone"],
    }
    (tmp_path / "artifacts" / "report").mkdir(parents=True)
    (tmp_path / "artifacts" / "report" / "metrics.json").write_text(json.dumps(report))
    result = verify_g5_published_metrics()
    assert result.passed is False


def test_fails_when_text_tier_disagreements_dont_match_corpus(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        {"capstone": set(), "llvm": set(), "spec": set(), "unicorn": set()},
    )
    _write_corpus(
        tmp_path / "artifacts" / "disagreements",
        [
            {
                "format_version": 1,
                "word": "0x5",
                "category": "MNEMONIC",
                "oracle_valid": {o: True for o in ("capstone", "llvm", "spec", "unicorn")},
                "oracle_text": {"capstone": "mov", "llvm": "mov", "spec": "movz", "unicorn": None},
            }
        ],
    )
    _write_g4_metrics(tmp_path / "artifacts" / "g4_metrics.json")
    report = {
        "format_version": 1,
        "total_words": SMALL_BITS,
        "spec_valid_count": 0,
        "per_tool": {
            # real: capstone differs from spec (mov != movz) -> 1, not 0
            "capstone": _base_per_tool(0, 100, 0),
            "llvm": _base_per_tool(0, 100, 0),
            "unicorn": _base_per_tool(0, 100, 0),
        },
        "tool_ranking_worst_first": ["capstone", "llvm", "unicorn"],
    }
    (tmp_path / "artifacts" / "report").mkdir(parents=True)
    (tmp_path / "artifacts" / "report" / "metrics.json").write_text(json.dumps(report))
    result = verify_g5_published_metrics()
    assert result.passed is False


def test_passes_on_correct_fixture(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        {"capstone": {5}, "llvm": set(), "spec": set(), "unicorn": set()},
    )
    _write_corpus(
        tmp_path / "artifacts" / "disagreements",
        [
            {
                "format_version": 1,
                "word": "0x9",
                "category": "MNEMONIC",
                "oracle_valid": {o: True for o in ("capstone", "llvm", "spec", "unicorn")},
                "oracle_text": {"capstone": "mov", "llvm": "movz", "spec": "movz", "unicorn": "movz"},
            }
        ],
    )
    _write_g4_metrics(tmp_path / "artifacts" / "g4_metrics.json")
    report = {
        "format_version": 1,
        "total_words": SMALL_BITS,
        "spec_valid_count": 0,
        "per_tool": {
            "capstone": _base_per_tool(1, 100, 1),
            "llvm": _base_per_tool(0, 100, 0),
            "unicorn": _base_per_tool(0, 100, 0),
        },
        "tool_ranking_worst_first": ["capstone", "llvm", "unicorn"],
    }
    (tmp_path / "artifacts" / "report").mkdir(parents=True)
    (tmp_path / "artifacts" / "report" / "metrics.json").write_text(json.dumps(report))
    result = verify_g5_published_metrics()
    assert result.passed is True, result.evidence
