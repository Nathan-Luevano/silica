from __future__ import annotations

import json
import os

from pysilica.verify.g2_exhaustive_coverage import (
    N_SHARDS,
    SHARD_BITS,
    verify_g2_exhaustive_coverage,
)


def _write_shard_records(tmp_path, override: dict | None = None, skip_id: int | None = None) -> None:
    d = tmp_path / "artifacts" / "sweep" / "shards"
    os.makedirs(d)
    for i in range(N_SHARDS):
        if i == skip_id:
            continue
        record = {
            "shard_id": i,
            "start": i * SHARD_BITS,
            "end": (i + 1) * SHARD_BITS,
            "oracles": ["capstone", "llvm", "spec", "unicorn"],
            "valid_counts": {"capstone": 0, "llvm": 0, "spec": 0, "unicorn": 0},
            "crash_count": 0,
            "untriaged_crash_count": 0,
            "content_hash": "deadbeef",
            "duration_ms": 1,
            "status": "complete",
        }
        if override and i == 0:
            record.update(override)
        (d / f"{i:03d}.json").write_text(json.dumps(record))


def test_fails_closed_with_no_shard_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = verify_g2_exhaustive_coverage()
    assert result.passed is False
    assert "missing" in result.evidence


def test_fails_with_missing_shard(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_shard_records(tmp_path, skip_id=5)
    result = verify_g2_exhaustive_coverage()
    assert result.passed is False


def test_fails_with_boundary_gap(tmp_path, monkeypatch):
    # deliberately broken fixture: shard 0 claims the wrong end offset,
    # simulating a gap/overlap in the [0, 2**32) tiling.
    monkeypatch.chdir(tmp_path)
    _write_shard_records(tmp_path, override={"end": SHARD_BITS + 1})
    result = verify_g2_exhaustive_coverage()
    assert result.passed is False
    assert result.evidence["reason"] == "shard boundary mismatch"


def test_fails_with_untriaged_crash_on_complete_shard(tmp_path, monkeypatch):
    # deliberately broken fixture: a shard claims "complete" while still
    # carrying an untriaged crash - design.md §9 forbids this combination.
    monkeypatch.chdir(tmp_path)
    _write_shard_records(tmp_path, override={"untriaged_crash_count": 3})
    result = verify_g2_exhaustive_coverage()
    assert result.passed is False
    assert "untriaged" in result.evidence["reason"]


def test_fails_without_bitmaps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_shard_records(tmp_path)
    result = verify_g2_exhaustive_coverage()
    assert result.passed is False
    assert "missing" in result.evidence
