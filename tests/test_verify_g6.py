from __future__ import annotations

import json

import zstandard

from pysilica.verify.g6_reproducers import verify_g6_reproducers

WORDS = [0x1000 + i for i in range(10)]


def _write_corpus(dir_):
    dir_.mkdir(parents=True, exist_ok=True)
    # all 10 fixture words land in shard 0 (2**24-bit shard, tiny offsets)
    records = [
        {
            "format_version": 1,
            "word": hex(w),
            "category": "OPERAND",
            "oracle_valid": {"capstone": True, "llvm": True, "spec": True, "unicorn": True},
            "oracle_text": {"capstone": "mov x0, x1", "llvm": "mov x0, x1", "spec": "orr x0, xzr, x1", "unicorn": "mov x0, x1"},
        }
        for w in WORDS
    ]
    cctx = zstandard.ZstdCompressor()
    body = "\n".join(json.dumps(r) for r in records).encode("utf-8")
    (dir_ / "000.zst").write_bytes(cctx.compress(body))


def _write_reproducer(path, word: int, **overrides) -> None:
    fields = {
        "word": hex(word),
        "category": "OPERAND",
        "tool": "capstone",
        "spec": "orr x0, xzr, x1",
        "actual": "mov x0, x1",
    }
    fields.update(overrides)
    body = "\n".join(f"- {k}: {v}" for k, v in fields.items())
    path.write_text(body + "\n\nfreeform explanation goes here.\n")


def test_fails_closed_with_no_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = verify_g6_reproducers()
    assert result.passed is False


def test_fails_with_too_few_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_corpus(tmp_path / "artifacts" / "disagreements")
    repro_dir = tmp_path / "artifacts" / "reproducers"
    repro_dir.mkdir(parents=True)
    for i in range(5):
        _write_reproducer(repro_dir / f"{i}.md", WORDS[i])
    result = verify_g6_reproducers()
    assert result.passed is False


def test_fails_with_fabricated_word(tmp_path, monkeypatch):
    # deliberately broken fixture: word 0xdeadbeef has no corpus record.
    monkeypatch.chdir(tmp_path)
    _write_corpus(tmp_path / "artifacts" / "disagreements")
    repro_dir = tmp_path / "artifacts" / "reproducers"
    repro_dir.mkdir(parents=True)
    for i in range(9):
        _write_reproducer(repro_dir / f"{i}.md", WORDS[i])
    _write_reproducer(repro_dir / "9.md", 0xDEADBEEF)
    result = verify_g6_reproducers()
    assert result.passed is False


def test_fails_with_duplicate_word(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_corpus(tmp_path / "artifacts" / "disagreements")
    repro_dir = tmp_path / "artifacts" / "reproducers"
    repro_dir.mkdir(parents=True)
    for i in range(9):
        _write_reproducer(repro_dir / f"{i}.md", WORDS[i])
    _write_reproducer(repro_dir / "9.md", WORDS[0])  # duplicate of file 0
    result = verify_g6_reproducers()
    assert result.passed is False


def test_fails_when_actual_doesnt_match_corpus(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_corpus(tmp_path / "artifacts" / "disagreements")
    repro_dir = tmp_path / "artifacts" / "reproducers"
    repro_dir.mkdir(parents=True)
    for i in range(9):
        _write_reproducer(repro_dir / f"{i}.md", WORDS[i])
    _write_reproducer(repro_dir / "9.md", WORDS[9], actual="something made up")
    result = verify_g6_reproducers()
    assert result.passed is False


def test_passes_on_correct_fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_corpus(tmp_path / "artifacts" / "disagreements")
    repro_dir = tmp_path / "artifacts" / "reproducers"
    repro_dir.mkdir(parents=True)
    for i in range(10):
        _write_reproducer(repro_dir / f"{i}.md", WORDS[i])
    result = verify_g6_reproducers()
    assert result.passed is True, result.evidence
