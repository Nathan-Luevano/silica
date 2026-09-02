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


def _without_other_sweep_evidence(root: Path, keep: str) -> None:
    if keep != "metrics":
        (root / "report" / "metrics.json").unlink(missing_ok=True)
    if keep != "g1":
        (root / "g1_metrics.json").unlink(missing_ok=True)


def test_empty_metrics_cannot_support_a_full_sweep_claim(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    (root / "report").mkdir(parents=True)
    (root / "report" / "metrics.json").write_text("{}")
    loaded = session.load(root)
    assert loaded.metrics.ok
    assert not loaded.has_sweep_evidence
    assert any("report/metrics.json" in problem for problem in loaded.problems())


def test_valid_metrics_alone_support_full_sweep_evidence(full_artifacts: Path) -> None:
    _without_other_sweep_evidence(full_artifacts, "metrics")
    loaded = session.load(full_artifacts)
    assert loaded.metrics.value.supports_sweep_evidence
    assert loaded.has_sweep_evidence


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("format_version", 2, "format_version is 2"),
        ("format_version", True, "not an integer"),
        ("total_words", model.TOTAL_WORDS - 1, "total_words is"),
        ("total_words", str(model.TOTAL_WORDS), "not an integer"),
        ("spec_valid_count", -1, "spec_valid_count is outside"),
        ("spec_valid_count", model.TOTAL_WORDS + 1, "spec_valid_count is outside"),
    ],
)
def test_invalid_metric_roots_do_not_support_sweep_evidence(
    full_artifacts: Path, field: str, value: object, message: str
) -> None:
    _without_other_sweep_evidence(full_artifacts, "metrics")
    path = full_artifacts / "report" / "metrics.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    loaded = session.load(full_artifacts)
    assert not loaded.has_sweep_evidence
    assert any(message in problem for problem in loaded.problems())


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_metric_never_crashes_the_reader(
    full_artifacts: Path, non_finite: float
) -> None:
    _without_other_sweep_evidence(full_artifacts, "metrics")
    path = full_artifacts / "report" / "metrics.json"
    data = json.loads(path.read_text())
    data["per_tool"]["capstone"]["validity_agreement_micro"] = non_finite
    path.write_text(json.dumps(data))
    loaded = session.load(full_artifacts)
    assert loaded.metrics.ok
    assert not loaded.has_sweep_evidence
    assert any("not a finite number" in problem for problem in loaded.problems())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("validity_disagreements_with_spec", -1, "validity disagreements are outside"),
        ("validity_agreement_micro", 0.5, "contradicts its disagreement count"),
        ("macro_validity_agreement", 2.0, "macro_validity_agreement is outside"),
        ("text_tier_disagreements_with_spec", -1, "must be nonnegative"),
        ("text_tier_agreement_micro", 0.5, "contradicts its counts"),
        ("text_tier_method", "estimated", "text_tier_method must be"),
        ("text_tier_sample_size", -1, "must be nonnegative"),
        ("text_tier_population", -1, "must be nonnegative"),
    ],
)
def test_contradictory_tool_metrics_do_not_support_sweep_evidence(
    full_artifacts: Path, field: str, value: object, message: str
) -> None:
    _without_other_sweep_evidence(full_artifacts, "metrics")
    path = full_artifacts / "report" / "metrics.json"
    data = json.loads(path.read_text())
    data["per_tool"]["capstone"][field] = value
    path.write_text(json.dumps(data))
    loaded = session.load(full_artifacts)
    assert not loaded.has_sweep_evidence
    assert any(message in problem for problem in loaded.problems())


def test_metric_tool_membership_and_ranking_are_evidence_invariants(
    full_artifacts: Path,
) -> None:
    _without_other_sweep_evidence(full_artifacts, "metrics")
    path = full_artifacts / "report" / "metrics.json"
    data = json.loads(path.read_text())
    data["per_tool"].pop("unicorn")
    data["tool_ranking_worst_first"] = ["capstone", "capstone", "llvm"]
    path.write_text(json.dumps(data))
    loaded = session.load(full_artifacts)
    assert not loaded.has_sweep_evidence
    joined = " ".join(loaded.problems())
    assert "per_tool keys must be exactly" in joined
    assert "contains duplicates" in joined
    assert "must contain exactly" in joined


def test_metric_text_sample_cannot_exceed_population(full_artifacts: Path) -> None:
    _without_other_sweep_evidence(full_artifacts, "metrics")
    path = full_artifacts / "report" / "metrics.json"
    data = json.loads(path.read_text())
    capstone = data["per_tool"]["capstone"]
    capstone["text_tier_sample_size"] = capstone["text_tier_population"] + 1
    path.write_text(json.dumps(data))
    loaded = session.load(full_artifacts)
    assert not loaded.has_sweep_evidence
    assert any("sample_size exceeds" in problem for problem in loaded.problems())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text_tier_method", "exhaustive"),
        ("text_tier_sample_size", 999999),
        ("text_tier_population", 1266064015),
    ],
)
def test_per_tool_text_denominators_must_agree(
    full_artifacts: Path, field: str, value: object
) -> None:
    _without_other_sweep_evidence(full_artifacts, "metrics")
    path = full_artifacts / "report" / "metrics.json"
    data = json.loads(path.read_text())
    data["per_tool"]["capstone"][field] = value
    if field == "text_tier_population":
        population = int(value)
        disagreements = data["per_tool"]["capstone"]["text_tier_disagreements_with_spec"]
        data["per_tool"]["capstone"]["text_tier_agreement_micro"] = (
            population - disagreements
        ) / population
    path.write_text(json.dumps(data))
    loaded = session.load(full_artifacts)
    assert not loaded.metrics_supports_sweep
    assert any(f"per-tool {field} values disagree" in problem for problem in loaded.problems())


def test_invalid_summaries_are_not_exposed_as_measurements(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    (root / "report").mkdir(parents=True)
    (root / "report" / "metrics.json").write_text("{}")
    (root / "g1_metrics.json").write_text(
        json.dumps({"spec_release": "invented", "allocated": model.TOTAL_WORDS})
    )
    loaded = session.load(root)
    assert not loaded.metrics_supports_sweep
    assert not loaded.g1_supports_sweep
    assert loaded.spec_release == "unknown"
    assert loaded.g1_value("allocated") is None


def test_empty_or_scalar_g1_metrics_cannot_support_a_sweep(tmp_path: Path) -> None:
    for payload in ({}, []):
        root = tmp_path / type(payload).__name__ / "artifacts"
        root.mkdir(parents=True)
        (root / "g1_metrics.json").write_text(json.dumps(payload))
        loaded = session.load(root)
        assert loaded.g1.ok
        assert not loaded.has_sweep_evidence
        assert any("g1_metrics.json" in problem for problem in loaded.problems())


def test_valid_g1_metrics_alone_support_full_sweep_evidence(full_artifacts: Path) -> None:
    _without_other_sweep_evidence(full_artifacts, "g1")
    loaded = session.load(full_artifacts)
    assert model.g1_evidence_problems(loaded.g1.value) == []
    assert loaded.has_sweep_evidence


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("spec_release", "", "spec_release"),
        ("tiling_files_checked", 0, "must be positive"),
        ("tiling_files_passed", 1, "does not equal"),
        ("allocated", -1, "allocated is outside"),
        ("unallocated", -1, "unallocated is outside"),
        ("ret_test_word", "0x00000000", "ret_test_word"),
        ("ret_test_passed", False, "ret_test_passed"),
    ],
)
def test_invalid_g1_summary_does_not_support_sweep_evidence(
    full_artifacts: Path, field: str, value: object, message: str
) -> None:
    _without_other_sweep_evidence(full_artifacts, "g1")
    path = full_artifacts / "g1_metrics.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    loaded = session.load(full_artifacts)
    assert not loaded.has_sweep_evidence
    assert any(message in problem for problem in loaded.problems())


def test_g1_allocated_and_unallocated_must_cover_the_space(full_artifacts: Path) -> None:
    _without_other_sweep_evidence(full_artifacts, "g1")
    path = full_artifacts / "g1_metrics.json"
    data = json.loads(path.read_text())
    data["unallocated"] -= 1
    path.write_text(json.dumps(data))
    loaded = session.load(full_artifacts)
    assert not loaded.has_sweep_evidence
    assert any("does not equal" in problem for problem in loaded.problems())


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


def _valid_shard_record(shard_id: int = 0) -> dict[str, object]:
    start = shard_id * model.SHARD_SIZE
    return {
        "shard_id": shard_id,
        "start": start,
        "end": start + model.SHARD_SIZE,
        "oracles": list(model.ORACLES),
        "valid_counts": {oracle: shard_id for oracle in model.ORACLES},
        "crash_count": 0,
        "untriaged_crash_count": 0,
        "content_hash": "a" * 64,
        "duration_ms": 1,
        "status": "complete",
    }


def _load_one_shard(tmp_path: Path, name: str, record: dict[str, object]) -> tuple[list[model.Shard], list[str]]:
    directory = tmp_path / "shards"
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(json.dumps(record))
    return model.load_shards(directory)


def test_valid_shard_record_loads_without_problems(tmp_path: Path) -> None:
    shards, problems = _load_one_shard(tmp_path, "000.json", _valid_shard_record())
    assert [shard.shard_id for shard in shards] == [0]
    assert problems == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("shard_id", True, "shard_id must be an integer"),
        ("shard_id", -1, "outside 0..255"),
        ("shard_id", 256, "outside 0..255"),
        ("start", None, "start must be an integer"),
        ("start", 1, "start is 1, expected 0"),
        ("end", 3, "end is 3, expected 16777216"),
        ("oracles", ["spec", "llvm", "capstone", "unicorn"], "oracles must be"),
        ("valid_counts", None, "valid_counts must be an object"),
        ("valid_counts", {"spec": 0}, "valid_counts keys must be exactly"),
        ("crash_count", -1, "crash_count must be nonnegative"),
        ("untriaged_crash_count", -1, "untriaged_crash_count must be nonnegative"),
        ("content_hash", "A" * 64, "content_hash is not"),
        ("content_hash", "a" * 63, "content_hash is not"),
        ("duration_ms", -1, "duration_ms must be nonnegative"),
        ("status", "done", "status must be"),
    ],
)
def test_invalid_shard_metadata_is_rejected(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    record = _valid_shard_record()
    record[field] = value
    shards, problems = _load_one_shard(tmp_path, "000.json", record)
    assert shards == []
    assert any(message in problem for problem in problems)


@pytest.mark.parametrize("bad_count", [-1, model.SHARD_SIZE + 1, True, 1.5, "7"])
def test_invalid_shard_valid_count_is_rejected(tmp_path: Path, bad_count: object) -> None:
    record = _valid_shard_record()
    counts = dict(record["valid_counts"])
    counts["spec"] = bad_count
    record["valid_counts"] = counts
    shards, problems = _load_one_shard(tmp_path, "000.json", record)
    assert shards == []
    assert any("valid_counts.spec" in problem for problem in problems)


def test_complete_shard_with_untriaged_crash_is_rejected(tmp_path: Path) -> None:
    record = _valid_shard_record()
    record["crash_count"] = 2
    record["untriaged_crash_count"] = 1
    shards, problems = _load_one_shard(tmp_path, "000.json", record)
    assert shards == []
    assert any("complete shard has untriaged crashes" in problem for problem in problems)


def test_untriaged_crashes_cannot_exceed_crashes(tmp_path: Path) -> None:
    record = _valid_shard_record()
    record["status"] = "crashed"
    record["untriaged_crash_count"] = 1
    shards, problems = _load_one_shard(tmp_path, "000.json", record)
    assert shards == []
    assert any("exceeds crash_count" in problem for problem in problems)


def test_shard_filename_must_match_embedded_id(tmp_path: Path) -> None:
    shards, problems = _load_one_shard(tmp_path, "007.json", _valid_shard_record(0))
    assert shards == []
    assert any("expected 000.json" in problem for problem in problems)


def test_duplicate_shard_id_is_rejected_even_under_an_extra_filename(tmp_path: Path) -> None:
    directory = tmp_path / "shards"
    directory.mkdir()
    record = _valid_shard_record(0)
    (directory / "000.json").write_text(json.dumps(record))
    (directory / "copy.json").write_text(json.dumps(record))
    shards, problems = model.load_shards(directory)
    assert [shard.shard_id for shard in shards] == [0]
    assert any("duplicate shard_id 0" in problem for problem in problems)


def test_malformed_256_file_set_cannot_claim_sweep_evidence(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    directory = root / "sweep" / "shards"
    directory.mkdir(parents=True)
    impossible = _valid_shard_record()
    impossible.update({"shard_id": 999, "start": 7, "end": 3})
    for shard_id in range(model.SHARD_COUNT):
        (directory / f"{shard_id:03d}.json").write_text(json.dumps(impossible))
    loaded = session.load(root)
    assert loaded.shards == []
    assert not loaded.has_sweep_evidence
    assert loaded.complete_shards == 0
    assert loaded.shard_problems


def _write_complete_shard_set(root: Path) -> None:
    directory = root / "sweep" / "shards"
    directory.mkdir(parents=True)
    for shard_id in range(model.SHARD_COUNT):
        record = _valid_shard_record(shard_id)
        (directory / f"{shard_id:03d}.json").write_text(json.dumps(record))


def test_exact_valid_shard_set_supports_sweep_evidence(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _write_complete_shard_set(root)
    loaded = session.load(root)
    assert [shard.shard_id for shard in loaded.shards] == list(range(model.SHARD_COUNT))
    assert loaded.complete_shards == model.SHARD_COUNT
    assert loaded.has_sweep_evidence
    assert loaded.shard_problems == []


def test_one_missing_shard_prevents_sweep_evidence(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _write_complete_shard_set(root)
    (root / "sweep" / "shards" / "137.json").unlink()
    loaded = session.load(root)
    assert len(loaded.shards) == model.SHARD_COUNT - 1
    assert loaded.complete_shards == model.SHARD_COUNT - 1
    assert not loaded.has_sweep_evidence


def test_one_corrupt_shard_prevents_evidence_and_surfaces_problem(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _write_complete_shard_set(root)
    path = root / "sweep" / "shards" / "137.json"
    record = _valid_shard_record(137)
    record["start"] = 0
    path.write_text(json.dumps(record))
    loaded = session.load(root)
    assert len(loaded.shards) == model.SHARD_COUNT - 1
    assert loaded.complete_shards == model.SHARD_COUNT - 1
    assert not loaded.has_sweep_evidence
    assert any("137.json: start is 0" in problem for problem in loaded.shard_problems)
    assert any("sweep/shards: 137.json" in problem for problem in loaded.problems())


def test_crashed_shard_with_triaged_count_is_structurally_valid(tmp_path: Path) -> None:
    record = _valid_shard_record()
    record["status"] = "crashed"
    record["crash_count"] = 3
    record["untriaged_crash_count"] = 2
    shards, problems = _load_one_shard(tmp_path, "000.json", record)
    assert len(shards) == 1
    assert shards[0].status == "crashed"
    assert shards[0].crash_count == 3
    assert shards[0].untriaged_crash_count == 2
    assert problems == []


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


def test_validity_disagreements_are_validity_only(full_artifacts: Path) -> None:
    corpus = Corpus(full_artifacts / "disagreements")
    # all four agree on validity here; the text differs, but the spec oracle
    # emits a bare mnemonic ("ADR"), so a raw string compare would flag every
    # tool on every text-tier record. that is not a measurement.
    operand = corpus.lookup(2)
    assert operand is not None
    assert operand.validity_disagreements() == []
    # a real validity split is reported
    validity = corpus.lookup(0)
    assert validity is not None
    assert validity.validity_disagreements() == ["capstone"]


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


def test_index_counts_both_json_spellings(full_artifacts: Path) -> None:
    # regression: probing one line and locking the shard to that spelling
    # dropped 441,360 of the real corpus's 1,000,000 text-tier records,
    # because a real shard mixes compact and spaced JSON in one file.
    raw = (full_artifacts / "disagreements" / "000.zst").read_bytes()
    import zstandard

    payload = zstandard.ZstdDecompressor().decompress(raw, max_output_size=1 << 20)
    assert b'"category":"VALIDITY"' in payload
    assert b'"category": "OPERAND"' in payload
    index = Corpus(full_artifacts / "disagreements").index_shard(0)
    assert index.counts == {"VALIDITY": 2, "OPERAND": 1}
    assert index.classified == index.total


def test_truncated_zst_is_reported_not_presented_as_complete(tmp_path: Path) -> None:
    import json

    import zstandard

    from silica_scope.corpus import StreamStatus

    payload = "".join(
        json.dumps(
            {
                "format_version": 1,
                "word": f"0x{w:08x}",
                "category": "VALIDITY",
                "oracle_valid": {"capstone": True, "llvm": False, "spec": False, "unicorn": False},
                "oracle_text": {"capstone": None, "llvm": None, "spec": None, "unicorn": None},
            },
            separators=(",", ":"),
        )
        + "\n"
        for w in range(20000)
    )
    whole = zstandard.ZstdCompressor().compress(payload.encode())
    out = tmp_path / "trunc" / "disagreements"
    out.mkdir(parents=True)
    (out / "000.zst").write_bytes(whole[: int(len(whole) * 0.4)])
    corpus = Corpus(out)
    status = StreamStatus()
    partial = list(corpus.iter_records(0, status=status))
    assert 0 < len(partial) < 20000
    assert status.truncated
    index = corpus.index_shard(0)
    assert index.truncated
    assert index.total < 20000


def test_unreadable_corpus_directory_does_not_raise(tmp_path: Path) -> None:
    import os

    root = tmp_path / "artifacts"
    (root / "disagreements").mkdir(parents=True)
    (root / "disagreements" / "000.zst").write_bytes(b"x")
    (root / "result_hash.txt").write_text("a" * 64)
    os.chmod(root / "disagreements", 0o000)
    try:
        corpus = Corpus(root / "disagreements")
        assert corpus.has_shard(0) is False
        assert corpus.shard_ids() == []
        assert corpus.shard_bytes(0) == 0
    finally:
        os.chmod(root / "disagreements", 0o755)


def test_sweep_evidence_needs_more_than_one_surviving_shard(full_artifacts: Path) -> None:
    from silica_scope import session as session_mod

    (full_artifacts / "report" / "metrics.json").unlink()
    (full_artifacts / "g1_metrics.json").unlink()
    loaded = session_mod.load(full_artifacts)
    # one shard record out of 256 does not support "all 2^32, not sampled"
    assert len(loaded.shards) == 1
    assert not loaded.has_sweep_evidence


def test_a_stray_goals_file_is_not_an_artifacts_tree(tmp_path: Path) -> None:
    # GOALS.yml is SILICA's own build-process tracking, unrelated to what
    # silica-scope reads - scope never looks at it, so a stray copy sitting
    # next to an empty artifacts/ dir must not count as "there's data here".
    from silica_scope import session as session_mod

    (tmp_path / "GOALS.yml").write_text("goals: []\n")
    loaded = session_mod.load(tmp_path / "artifacts")
    assert not loaded.has_anything
