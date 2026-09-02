from __future__ import annotations

import io
import os
import stat
from pathlib import Path

import pytest

from scripts import check_worklog_drift as drift

PASS_LINES = "\n".join(
    f"[PASS] G{i}  evidence={{'goal': {i}}}  measured={{'value': {i}}}" for i in range(1, 8)
) + "\n"

FAIL_LINES = PASS_LINES.replace("[PASS] G4", "[FAIL] G4")


def _write_worklog(path: Path, verifier_state: str = "G1-G7 passing") -> None:
    path.write_text(
        "# Worklog\n\n"
        "## 2026-09-02 — abc1234 — test: example\n\n"
        f"**Verifier state:** {verifier_state}\n\n"
        "**Next:** none.\n"
    )


def _run(output: str = PASS_LINES, returncode: int = 0) -> drift.VerifyRun:
    return drift.VerifyRun(output, returncode, drift.parse_results(output))


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (PASS_LINES, tuple(("PASS", f"G{i}") for i in range(1, 8))),
        (FAIL_LINES, tuple(("FAIL" if i == 4 else "PASS", f"G{i}") for i in range(1, 8))),
        ("noise\n[PASS] G1 evidence={}\nmore noise\n", (("PASS", "G1"),)),
        ("[PASS] G8 evidence={}\n[OK] G1\n", ()),
        ("prefix [PASS] G1 evidence={}\n", ()),
    ],
)
def test_parse_results_only_accepts_goal_result_lines(output, expected):
    assert drift.parse_results(output) == expected


def test_verify_run_counts_results_and_formats_summary():
    run = _run(FAIL_LINES, 9)
    assert run.passing == 6
    assert run.failing == 1
    assert run.summary == "6 passing, 1 failing (exit 9)"


def test_verify_run_requires_all_goals_once_in_order():
    assert _run().has_complete_result_set is True
    assert _run(PASS_LINES.replace("[PASS] G4", "[PASS] G3")).has_complete_result_set is False
    assert _run(PASS_LINES.replace("[PASS] G4", "[PASS] G5")).has_complete_result_set is False


def test_verify_run_rejects_missing_result():
    lines = PASS_LINES.splitlines()
    assert _run("\n".join(lines[:-1]) + "\n").has_complete_result_set is False


def test_verify_run_rejects_duplicate_result():
    output = PASS_LINES + "[PASS] G7 evidence={} measured={}\n"
    assert _run(output).has_complete_result_set is False


def test_gate_accepts_exact_seven_passes(capsys):
    assert drift.gate_result(_run()) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("returncode", [1, 2, 17, 255])
def test_gate_propagates_normal_failure_exit_codes(returncode):
    assert drift.gate_result(_run(FAIL_LINES, returncode)) == returncode


@pytest.mark.parametrize("returncode", [-9, 256, 300])
def test_gate_normalizes_non_shell_exit_codes(returncode):
    assert drift.gate_result(_run(FAIL_LINES, returncode)) == 1


def test_gate_fails_when_result_says_fail_but_process_exits_zero(capsys):
    assert drift.gate_result(_run(FAIL_LINES, 0)) == 1
    assert "reported failures with exit 0" in capsys.readouterr().err


@pytest.mark.parametrize(
    "output",
    [
        "",
        "silica crashed before reporting\n",
        PASS_LINES.replace("[PASS] G7", ""),
        PASS_LINES + "[PASS] G1 evidence={} measured={}\n",
        PASS_LINES.replace("[PASS] G3", "[PASS] G4"),
    ],
)
def test_gate_fails_closed_on_incomplete_or_ambiguous_results(output, capsys):
    assert drift.gate_result(_run(output, 0)) == 1
    assert "incomplete verifier result set" in capsys.readouterr().err


def test_last_entry_returns_newest_verifier_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "WORKLOG.md").write_text(
        "# Worklog\n\n"
        "## old\n\n"
        "**Verifier state:** zero passing.\n\n"
        "**Next:** old.\n\n"
        "## CHECKPOINT\n\n"
        "No regular fields here.\n\n"
        "## new\n\n"
        "**Verifier state:** seven passing.\n\n"
        "**Next:** new.\n"
    )
    assert drift.last_entry_verifier_state() == "seven passing."


def test_last_entry_walks_past_checkpoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "WORKLOG.md").write_text(
        "## regular\n\n"
        "**Verifier state:** six passing.\n\n"
        "**Next:** audit.\n\n"
        "## CHECKPOINT\n\n"
        "Verifier narrative without the required field.\n"
    )
    assert drift.last_entry_verifier_state() == "six passing."


def test_last_entry_returns_none_without_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert drift.last_entry_verifier_state() is None


def test_last_entry_returns_none_without_field(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "WORKLOG.md").write_text("# Worklog\n\n## entry\n\nNo state here.\n")
    assert drift.last_entry_verifier_state() is None


def test_drift_skips_absent_local_worklog(capsys):
    status = drift.drift_result(None, "7 passing, 0 failing (exit 0)", worklog_exists=False)
    captured = capsys.readouterr()
    assert status == 0
    assert "no local WORKLOG.md" in captured.out
    assert captured.err == ""


def test_drift_rejects_worklog_without_verifier_state(capsys):
    status = drift.drift_result(None, "7 passing, 0 failing (exit 0)", worklog_exists=True)
    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert "no WORKLOG.md entry" in captured.err


@pytest.mark.parametrize(
    "claim",
    [
        "G1-G7 passing.",
        "All goals passing after the run.",
        "Seven passing and no failures.",
    ],
)
def test_drift_rejects_all_failing_against_passing_claim(claim, capsys):
    status = drift.drift_result(claim, "0 passing, 7 failing (exit 1)", worklog_exists=True)
    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert "but silica verify reports" in captured.err


@pytest.mark.parametrize(
    "claim",
    [
        "G1 not passing.",
        "G1 failing as expected.",
        "G1 remains failing.",
    ],
)
def test_drift_preserves_noncontradictory_failure_claims(claim, capsys):
    status = drift.drift_result(claim, "0 passing, 7 failing (exit 1)", worklog_exists=True)
    captured = capsys.readouterr()
    assert status == 0
    assert "no contradiction detected" in captured.out
    assert captured.err == ""


def test_drift_accepts_current_passing_state(capsys):
    status = drift.drift_result(
        "`make check` clean immediately before commit.",
        "7 passing, 0 failing (exit 0)",
        worklog_exists=True,
    )
    captured = capsys.readouterr()
    assert status == 0
    assert "7 passing, 0 failing (exit 0)" in captured.out
    assert captured.err == ""


def _write_fake_python(path: Path, output: str, returncode: int) -> Path:
    script = path / "python"
    quoted_output = repr(output)
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n"
        "counter = Path(os.environ['VERIFY_COUNTER'])\n"
        "count = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(count + 1))\n"
        f"print({quoted_output}, end='')\n"
        f"raise SystemExit({returncode})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_run_verify_invokes_command_once_and_captures_results(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_python(bin_dir, PASS_LINES, 0)
    counter = tmp_path / "count"
    monkeypatch.setenv("VERIFY_COUNTER", str(counter))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    run = drift.run_verify(show_output=False)

    assert counter.read_text() == "1"
    assert run.output == PASS_LINES
    assert run.returncode == 0
    assert run.has_complete_result_set is True


def test_run_verify_streams_the_same_bytes_it_captures(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_python(bin_dir, FAIL_LINES, 3)
    counter = tmp_path / "count"
    monkeypatch.setenv("VERIFY_COUNTER", str(counter))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    run = drift.run_verify(show_output=True)

    captured = capsys.readouterr()
    assert counter.read_text() == "1"
    assert captured.out == FAIL_LINES
    assert run.output == captured.out
    assert run.returncode == 3


def test_main_gate_runs_once_checks_drift_and_passes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_worklog(tmp_path / "WORKLOG.md")
    calls = 0

    def fake_run_verify(*, show_output: bool):
        nonlocal calls
        calls += 1
        assert show_output is True
        return _run()

    monkeypatch.setattr(drift, "run_verify", fake_run_verify)

    assert drift.main(["--gate"]) == 0
    assert calls == 1
    assert "no contradiction detected" in capsys.readouterr().out


def test_main_gate_runs_verifier_without_local_worklog(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    calls = 0

    def fake_run_verify(*, show_output: bool):
        nonlocal calls
        calls += 1
        assert show_output is True
        return _run()

    monkeypatch.setattr(drift, "run_verify", fake_run_verify)

    assert drift.main(["--gate"]) == 0
    assert calls == 1
    assert "skipping" in capsys.readouterr().out


def test_main_gate_propagates_verifier_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_worklog(tmp_path / "WORKLOG.md", "G4 failing.")
    monkeypatch.setattr(
        drift,
        "run_verify",
        lambda *, show_output: _run(FAIL_LINES, 23),
    )
    assert drift.main(["--gate"]) == 23


def test_main_gate_fails_on_bad_worklog_even_when_verifier_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "WORKLOG.md").write_text("# no regular verifier state\n")
    monkeypatch.setattr(drift, "run_verify", lambda *, show_output: _run())
    assert drift.main(["--gate"]) == 1


def test_main_without_gate_preserves_drift_only_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_worklog(tmp_path / "WORKLOG.md", "G4 failing.")
    observed: list[bool] = []

    def fake_run_verify(*, show_output: bool):
        observed.append(show_output)
        return _run(FAIL_LINES, 4)

    monkeypatch.setattr(drift, "run_verify", fake_run_verify)

    assert drift.main([]) == 0
    assert observed == [False]


def test_main_reports_verifier_spawn_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_worklog(tmp_path / "WORKLOG.md")

    def fail_to_spawn(*, show_output: bool):
        raise FileNotFoundError("python missing")

    monkeypatch.setattr(drift, "run_verify", fail_to_spawn)

    assert drift.main(["--gate"]) == 1
    assert "could not run silica verify" in capsys.readouterr().err


def test_parser_defaults_to_drift_only():
    assert drift.build_parser().parse_args([]).gate is False


def test_parser_enables_combined_gate():
    assert drift.build_parser().parse_args(["--gate"]).gate is True


def test_emit_flushes_immediately():
    class RecordingStream(io.StringIO):
        def __init__(self):
            super().__init__()
            self.flushes = 0

        def flush(self):
            self.flushes += 1
            super().flush()

    stream = RecordingStream()
    drift._emit("[PASS] G1\n", stream)
    assert stream.getvalue() == "[PASS] G1\n"
    assert stream.flushes == 1
