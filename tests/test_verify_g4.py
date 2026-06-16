from __future__ import annotations

import json
import os

import zstandard

from pysilica.verify import g4_disagreement_corpus as g4mod
from pysilica.verify.g4_disagreement_corpus import verify_g4_disagreement_corpus

SMALL_BITS = 1 << 8  # 256 words, 32 bytes per oracle - fast fixture bitmaps


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


def _write_metrics(path, **overrides) -> None:
    base = {
        "format_version": 1,
        "shards_with_disagreements": 1,
        "total_disagreements": 1,
        "category_counts": {"VALIDITY": 1},
        "validity_tier_exhaustive": True,
        "validity_disagreements": 1,
        "text_tier_method": "exhaustive",
    }
    base.update(overrides)
    path.write_text(json.dumps(base))


def _setup(tmp_path, monkeypatch, bitmaps: dict[str, set[int]]):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(g4mod, "TOTAL_BITS", SMALL_BITS)
    monkeypatch.setattr(g4mod, "SAMPLE_SIZE", 500)
    os.makedirs(tmp_path / "artifacts" / "bitmaps")
    for oracle, bits in bitmaps.items():
        _write_bitmap(tmp_path / "artifacts" / "bitmaps" / f"{oracle}.bin", bits)


def test_fails_closed_with_no_corpus(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = verify_g4_disagreement_corpus()
    assert result.passed is False


def test_fails_when_validity_disagreement_missing_from_corpus(tmp_path, monkeypatch):
    # deliberately broken fixture: word 5 genuinely disagrees (spec says
    # valid, others don't) in the bitmaps, but the corpus has no record
    # for it - the cross-check against real bitmap data must catch this.
    _setup(
        tmp_path,
        monkeypatch,
        {"capstone": set(), "llvm": set(), "spec": {5}, "unicorn": set()},
    )
    _write_corpus(tmp_path / "artifacts" / "disagreements", [])
    _write_metrics(tmp_path / "artifacts" / "g4_metrics.json", total_disagreements=0, category_counts={})
    result = verify_g4_disagreement_corpus()
    assert result.passed is False


def test_fails_with_bad_category(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {o: set() for o in g4mod.ORACLES})
    _write_corpus(
        tmp_path / "artifacts" / "disagreements",
        [{"format_version": 1, "word": "0x5", "category": "NOT_REAL", "oracle_valid": {o: False for o in g4mod.ORACLES}}],
    )
    _write_metrics(tmp_path / "artifacts" / "g4_metrics.json")
    result = verify_g4_disagreement_corpus()
    assert result.passed is False


def test_fails_when_sampled_without_sample_size(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {o: set() for o in g4mod.ORACLES})
    _write_corpus(tmp_path / "artifacts" / "disagreements", [])
    _write_metrics(tmp_path / "artifacts" / "g4_metrics.json", text_tier_method="sampled")
    result = verify_g4_disagreement_corpus()
    assert result.passed is False


def test_passes_on_correct_fixture(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        {"capstone": set(), "llvm": set(), "spec": {5}, "unicorn": set()},
    )
    _write_corpus(
        tmp_path / "artifacts" / "disagreements",
        [
            {
                "format_version": 1,
                "word": "0x5",
                "category": "VALIDITY",
                "oracle_valid": {"capstone": False, "llvm": False, "spec": True, "unicorn": False},
                "oracle_text": {"capstone": None, "llvm": None, "spec": "UDF", "unicorn": None},
            }
        ],
    )
    _write_metrics(
        tmp_path / "artifacts" / "g4_metrics.json",
        total_disagreements=1,
        category_counts={"VALIDITY": 1},
        validity_disagreements=1,
    )
    result = verify_g4_disagreement_corpus()
    assert result.passed is True
