from __future__ import annotations

import hashlib
import json

from pysilica.verify.g7_reproducibility import verify_g7_reproducibility

GOOD_ENV = """
name: silica
dependencies:
  - python=3.11
  - rust=1.79.0
  - capstone=5.0.1
  - llvmdev=18.1.8
  - llvm-tools=18.1.8
  - unicorn=2.0.1
"""

GOOD_MAKEFILE = """
check:
	cargo test

all:
	python -m pysilica.spec.compile-spec
	target/release/silica-sweep run --shard $$N
	python -m pysilica.analyze.g4_run
	python -m pysilica.analyze.g5_report
	python -m pysilica.analyze.g6_reproducers
	python scripts/compute_result_hash.py > artifacts/result_hash.txt
"""


def _write_artifacts(tmp_path, reproducers: int = 2) -> str:
    art = tmp_path / "artifacts"
    (art / "bitmaps").mkdir(parents=True)
    (art / "report").mkdir(parents=True)
    (art / "reproducers").mkdir(parents=True)

    (art / "decode-table.bin").write_bytes(b"decode-table-bytes")
    for o in ("capstone", "llvm", "spec", "unicorn"):
        (art / "bitmaps" / f"{o}.bin").write_bytes(f"{o}-bitmap".encode())
    (art / "g4_metrics.json").write_text(json.dumps({"total_disagreements": 1}))
    (art / "report" / "metrics.json").write_text(json.dumps({"spec_valid_count": 1}))
    for i in range(reproducers):
        (art / "reproducers" / f"{i}.md").write_text(f"reproducer {i}\n")

    h = hashlib.sha256()
    h.update((art / "decode-table.bin").read_bytes())
    for o in ("capstone", "llvm", "spec", "unicorn"):
        h.update((art / "bitmaps" / f"{o}.bin").read_bytes())
    h.update((art / "g4_metrics.json").read_bytes())
    h.update((art / "report" / "metrics.json").read_bytes())
    for p in sorted((art / "reproducers").glob("*.md")):
        h.update(p.read_bytes())
    return h.hexdigest()


def _setup(tmp_path, monkeypatch, env=GOOD_ENV, makefile=GOOD_MAKEFILE) -> str:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "environment.yml").write_text(env)
    (tmp_path / "Makefile").write_text(makefile)
    return _write_artifacts(tmp_path)


def test_fails_closed_with_no_environment_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = verify_g7_reproducibility()
    assert result.passed is False


def test_fails_with_unpinned_tool_version(tmp_path, monkeypatch):
    bad_env = GOOD_ENV.replace("capstone=5.0.1", "capstone")
    real_hash = _setup(tmp_path, monkeypatch, env=bad_env)
    (tmp_path / "artifacts" / "result_hash.txt").write_text(real_hash)
    result = verify_g7_reproducibility()
    assert result.passed is False


def test_fails_when_makefile_missing_pipeline_step(tmp_path, monkeypatch):
    bad_makefile = GOOD_MAKEFILE.replace("g6_reproducers", "")
    real_hash = _setup(tmp_path, monkeypatch, makefile=bad_makefile)
    (tmp_path / "artifacts" / "result_hash.txt").write_text(real_hash)
    result = verify_g7_reproducibility()
    assert result.passed is False


def test_fails_when_hash_doesnt_match_real_artifacts(tmp_path, monkeypatch):
    # deliberately broken fixture: claimed hash is stale/fabricated,
    # doesn't match what's actually on disk.
    _setup(tmp_path, monkeypatch)
    (tmp_path / "artifacts" / "result_hash.txt").write_text("0" * 64)
    result = verify_g7_reproducibility()
    assert result.passed is False


def test_passes_on_correct_fixture(tmp_path, monkeypatch):
    real_hash = _setup(tmp_path, monkeypatch)
    (tmp_path / "artifacts" / "result_hash.txt").write_text(real_hash)
    result = verify_g7_reproducibility()
    assert result.passed is True, result.evidence
