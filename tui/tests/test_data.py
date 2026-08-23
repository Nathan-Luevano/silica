from __future__ import annotations

import json
from pathlib import Path

import pytest

from silica_scope import bits, discovery, fmt, model, session
from silica_scope.corpus import Corpus, CorpusUnavailable, parse_record


def test_locate_prefers_explicit_path(tmp_path: Path) -> None:
    assert discovery.locate(tmp_path) == tmp_path.resolve()


def test_locate_walks_up_to_an_artifacts_dir(full_artifacts: Path) -> None:
    nested = full_artifacts.parent / "a" / "b"
    nested.mkdir(parents=True)
    assert discovery.locate(None, cwd=nested) == full_artifacts.resolve()


def test_locate_falls_back_to_cwd_artifacts(tmp_path: Path) -> None:
    assert discovery.locate(None, cwd=tmp_path) == (tmp_path / "artifacts").resolve()


def test_scan_reports_presence(full_artifacts: Path) -> None:
    found = discovery.scan(full_artifacts)
    assert found.has("metrics")
    assert found.has("reproducers")
    assert not found.has("bitmaps")
    assert found.goals_file is not None


def test_metrics_parse(full_artifacts: Path) -> None:
    loaded = model.load_metrics(full_artifacts / "report" / "metrics.json")
    assert loaded.ok
    assert loaded.value.ranking_worst_first[0] == "capstone"
    assert loaded.value.warnings == []


def test_metrics_survives_a_malformed_per_tool(corrupt_artifacts: Path) -> None:
    loaded = model.load_metrics(corrupt_artifacts / "report" / "metrics.json")
    assert loaded.ok
    assert loaded.value.per_tool == {}
    assert any("per_tool" in w for w in loaded.value.warnings)


def test_metrics_reports_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    path.write_text("{ nope")
    loaded = model.load_metrics(path)
    assert not loaded.ok
    assert "invalid JSON" in loaded.error


def test_result_hash_validation(tmp_path: Path) -> None:
    good = tmp_path / "good.txt"
    good.write_text("f" * 64 + "\n")
    assert model.load_result_hash(good).ok
    bad = tmp_path / "bad.txt"
    bad.write_text("deadbeef")
    assert model.load_result_hash(bad).error


def test_shard_problems_are_collected_not_raised(corrupt_artifacts: Path) -> None:
    shards, problems = model.load_shards(corrupt_artifacts / "sweep" / "shards")
    assert shards == []
    assert problems


def test_reproducer_parsing(full_artifacts: Path) -> None:
    repros = model.load_reproducers(full_artifacts / "reproducers")
    assert len(repros) == 1
    assert repros[0].word_int == 0x109B485A
    assert repros[0].shard_id == 0x10
    assert repros[0].problems == []
    assert "prose" in repros[0].body


def test_reproducer_flags_its_own_defects(corrupt_artifacts: Path) -> None:
    repro = model.load_reproducers(corrupt_artifacts / "reproducers")[0]
    joined = " ".join(repro.problems)
    assert "taxonomy" in joined
    assert "not one of" in joined
    assert "not a disagreement" in joined


def test_goals_load(full_artifacts: Path) -> None:
    goals, error = model.load_goals(full_artifacts.parent / "GOALS.yml")
    assert error == ""
    assert goals[0].id == "G1"
    assert goals[0].status == "pass"
    assert goals[0].verifier_sha256


def test_session_problem_list_ignores_absent_files(published_artifacts: Path) -> None:
    loaded = session.load(published_artifacts)
    assert loaded.problems() == []
    assert not loaded.has_sweep_evidence
    assert loaded.has_anything


def test_session_problem_list_reports_broken_files(corrupt_artifacts: Path) -> None:
    loaded = session.load(corrupt_artifacts)
    joined = " ".join(loaded.problems())
    assert "g4_metrics.json" in joined
    assert "result_hash.txt" in joined


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0xd65f03c0", 0xD65F03C0),
        ("d65f03c0", 0xD65F03C0),
        ("D65F03C0", 0xD65F03C0),
        ("d65f_03c0", 0xD65F03C0),
        ("1" * 32, 0xFFFFFFFF),
        ("0" * 32, 0),
        ("zzz", None),
        ("", None),
        ("1ffffffff", None),
    ],
)
def test_word_parsing(text: str, expected: int | None) -> None:
    assert bits.parse_word(text) == expected


def test_encoding_group() -> None:
    assert bits.group_of(0x109B485A) == "Data Processing -- Immediate"
    assert bits.group_of(0xD65F03C0) == "Branches, Exception, System"
    assert bits.group_of(0x04000000) == "SVE"


def test_bit_rows_are_aligned() -> None:
    grouped, ruler = bits.bit_rows(0x109B485A)
    assert grouped == "0001 0000 1001 1011 0100 1000 0101 1010"
    assert ruler.split()[0] == "31"


def test_fine_bar_separates_close_values() -> None:
    assert fmt.fine_bar(0.8479, 26) != fmt.fine_bar(0.8826, 26)
    assert len(fmt.fine_bar(0.5, 26)) == 26


def test_record_rejects_unknown_format_version() -> None:
    line = json.dumps({"format_version": 99, "word": "0x0", "category": "VALIDITY"}).encode()
    assert parse_record(line) is None


def test_record_rejects_garbage() -> None:
    assert parse_record(b"not json") is None
    assert parse_record(b'{"format_version": 1}') is None


def test_corpus_reads_compact_json(full_artifacts: Path) -> None:
    corpus = Corpus(full_artifacts / "disagreements")
    assert corpus.shard_ids() == [0]
    records = list(corpus.iter_records(0))
    assert [r.hex for r in records] == ["0x00000000", "0x00000001", "0x00000002"]


def test_corpus_category_filter(full_artifacts: Path) -> None:
    corpus = Corpus(full_artifacts / "disagreements")
    operands = list(corpus.iter_records(0, categories=frozenset({"OPERAND"})))
    assert len(operands) == 1
    assert operands[0].word == 2


def test_corpus_index_counts_every_line(full_artifacts: Path) -> None:
    index = Corpus(full_artifacts / "disagreements").index_shard(0)
    assert index.total == 3
    assert index.counts == {"VALIDITY": 2, "OPERAND": 1}
    assert index.bad_lines == 0


def test_corpus_lookup_and_miss(full_artifacts: Path) -> None:
    corpus = Corpus(full_artifacts / "disagreements")
    assert corpus.lookup(2) is not None
    assert corpus.lookup(3) is None


def test_disagreeing_tools_uses_text_as_well_as_validity(full_artifacts: Path) -> None:
    record = Corpus(full_artifacts / "disagreements").lookup(2)
    assert record is not None
    # unicorn's text is the "<valid>" placeholder, not a real disassembly -
    # counting it as a text disagreement would invent one nobody measured.
    assert set(record.disagreeing_tools()) == {"capstone", "llvm"}


def test_placeholder_text_is_not_a_disagreement() -> None:
    from silica_scope.corpus import is_placeholder

    assert is_placeholder("<valid>")
    assert is_placeholder(None)
    assert not is_placeholder("adr x26, #1")


def test_corrupt_zst_raises_a_typed_error(corrupt_artifacts: Path) -> None:
    corpus = Corpus(corrupt_artifacts / "disagreements")
    with pytest.raises(CorpusUnavailable):
        list(corpus.iter_records(0))


def test_missing_shard_raises_a_typed_error(full_artifacts: Path) -> None:
    corpus = Corpus(full_artifacts / "disagreements")
    with pytest.raises(CorpusUnavailable):
        list(corpus.iter_records(7))
