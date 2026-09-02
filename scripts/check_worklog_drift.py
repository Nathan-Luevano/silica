from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

VERIFY_COMMAND = ("python", "-m", "pysilica.cli", "verify")
GOAL_IDS = tuple(f"G{i}" for i in range(1, 8))
RESULT_LINE = re.compile(r"^\[(PASS|FAIL)\]\s+(G[1-7])(?:\s|$)")


@dataclass(frozen=True)
class VerifyRun:
    output: str
    returncode: int
    results: tuple[tuple[str, str], ...]

    @property
    def passing(self) -> int:
        return sum(status == "PASS" for status, _ in self.results)

    @property
    def failing(self) -> int:
        return sum(status == "FAIL" for status, _ in self.results)

    @property
    def has_complete_result_set(self) -> bool:
        return tuple(goal_id for _, goal_id in self.results) == GOAL_IDS

    @property
    def summary(self) -> str:
        return f"{self.passing} passing, {self.failing} failing (exit {self.returncode})"


def parse_results(output: str) -> tuple[tuple[str, str], ...]:
    results: list[tuple[str, str]] = []
    for line in output.splitlines():
        match = RESULT_LINE.match(line)
        if match is not None:
            results.append((match.group(1), match.group(2)))
    return tuple(results)


def _emit(line: str, stream: TextIO) -> None:
    stream.write(line)
    stream.flush()


def run_verify(*, show_output: bool) -> VerifyRun:
    # stream the seven result lines when this is the real gate. Keeping the
    # pipe lets the same bytes drive the drift check without a second scan.
    process = subprocess.Popen(
        VERIFY_COMMAND,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is None:
        process.kill()
        process.wait()
        raise RuntimeError("silica verify stdout pipe was not created")

    chunks: list[str] = []
    for line in process.stdout:
        chunks.append(line)
        if show_output:
            _emit(line, sys.stdout)
    process.stdout.close()
    returncode = process.wait()
    output = "".join(chunks)
    return VerifyRun(output, returncode, parse_results(output))


def last_entry_verifier_state() -> str | None:
    path = Path("WORKLOG.md")
    if not path.exists():
        return None
    text = path.read_text()
    entries = re.split(r"\n(?=## )", text.strip())
    # CHECKPOINT entries (§10.4) use a different template with no "Verifier
    # state:" field; walk backwards to the last regular commit entry.
    for entry in reversed(entries):
        match = re.search(r"^\*\*Verifier state:\*\*\s*(.+)$", entry, re.MULTILINE)
        if match:
            return match.group(1).strip()
    return None


def drift_result(claimed: str | None, actual: str, *, worklog_exists: bool) -> int:
    if not worklog_exists:
        print("check_worklog_drift: no local WORKLOG.md, skipping (nothing published to check)")
        return 0

    if claimed is None:
        print(
            "check_worklog_drift: no WORKLOG.md entry with a Verifier state: line",
            file=sys.stderr,
        )
        return 1

    # the claimed line is prose ("G1 failing - decode tree not built yet");
    # this only checks the failing/passing counts don't contradict it, since
    # the exact wording is free text by design (§10.3).
    claimed_has_passing_claim = "passing" in claimed.lower() and "not" not in claimed.lower()
    actual_all_failing = actual.startswith("0 passing")

    if claimed_has_passing_claim and actual_all_failing:
        print(
            f"check_worklog_drift: WORKLOG.md claims '{claimed}' but silica verify reports {actual}",
            file=sys.stderr,
        )
        return 1

    print(f"check_worklog_drift: claimed='{claimed}' actual='{actual}' (no contradiction detected)")
    return 0


def gate_result(run: VerifyRun) -> int:
    if not run.has_complete_result_set:
        seen = [goal_id for _, goal_id in run.results]
        print(
            f"check_worklog_drift: incomplete verifier result set: expected {list(GOAL_IDS)}, "
            f"got {seen}",
            file=sys.stderr,
        )
        return 1
    if run.returncode != 0:
        return run.returncode if 0 < run.returncode < 256 else 1
    if run.failing:
        print(
            "check_worklog_drift: silica verify reported failures with exit 0",
            file=sys.stderr,
        )
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gate",
        action="store_true",
        help="stream and gate on the same verifier run used for the drift check",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    worklog_exists = Path("WORKLOG.md").exists()
    claimed = last_entry_verifier_state() if worklog_exists else None

    try:
        run = run_verify(show_output=args.gate)
    except OSError as exc:
        print(f"check_worklog_drift: could not run silica verify: {exc}", file=sys.stderr)
        return 1

    drift_status = drift_result(claimed, run.summary, worklog_exists=worklog_exists)
    if not args.gate:
        return drift_status

    verify_status = gate_result(run)
    return verify_status or drift_status


if __name__ == "__main__":
    raise SystemExit(main())
