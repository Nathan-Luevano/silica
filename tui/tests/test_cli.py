from __future__ import annotations

from pathlib import Path

import pytest

from silica_scope import __version__
from silica_scope.cli import build_parser, main, report


def test_version_flag_exits_zero_and_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_flag_documents_report_and_the_path_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--report" in out
    assert "path" in out


def test_main_report_on_a_real_tree_exits_zero_and_prints_the_headline(
    full_artifacts: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--report", str(full_artifacts)])
    assert code == 0
    out = capsys.readouterr().out
    assert "encodings swept" in out
    assert "capstone" in out


def test_main_report_on_an_empty_tree_exits_one_without_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--report", str(tmp_path / "nothing-here")])
    assert code == 1
    out = capsys.readouterr().out
    assert "no SILICA artifacts under" in out
    assert "silica-scope /path/to/silica/artifacts" in out


def test_main_report_on_a_published_checkout_still_exits_zero(
    published_artifacts: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # a published checkout only ships reproducers/ and result_hash.txt - that
    # is real content, not the empty state, and --report must say so, not 1.
    code = main(["--report", str(published_artifacts)])
    assert code == 0
    out = capsys.readouterr().out
    assert "no sweep artifacts here" in out


def test_main_report_on_corrupt_artifacts_lists_problems_not_a_traceback(
    corrupt_artifacts: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--report", str(corrupt_artifacts)])
    assert code == 0
    out = capsys.readouterr().out
    assert "artifact problem" in out


def test_report_does_not_claim_a_sweep_from_empty_metrics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "artifacts"
    (root / "report").mkdir(parents=True)
    (root / "report" / "metrics.json").write_text("{}")
    assert report(root) == 0
    out = capsys.readouterr().out
    assert "artifact problem" in out
    assert "all 2^32, not sampled" not in out
    assert "no sweep artifacts here" in out
    assert "no per-tool metrics" in out


def test_report_function_returns_the_same_codes_as_main(
    full_artifacts: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert report(full_artifacts) == 0
    capsys.readouterr()
    assert report(tmp_path / "empty") == 1


def test_main_resolves_an_explicit_path_argument(
    full_artifacts: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # discovery.locate treats an explicit path as authoritative, unresolved
    # relative to any $SILICA_ARTIFACTS or upward search - confirm the CLI
    # actually passes the positional argument through to it.
    code = main(["--report", str(full_artifacts)])
    assert code == 0
    assert str(full_artifacts) in capsys.readouterr().out
