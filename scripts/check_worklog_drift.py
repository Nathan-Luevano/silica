from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


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


def actual_verify_summary() -> str:
    result = subprocess.run(
        ["python", "-m", "pysilica.cli", "verify"], capture_output=True, text=True, check=False
    )
    passing = result.stdout.count("[PASS]")
    failing = result.stdout.count("[FAIL]")
    return f"{passing} passing, {failing} failing (exit {result.returncode})"


def main() -> int:
    # WORKLOG.md is Nathan's local dev log, deliberately not published -
    # a clean checkout without it isn't a drift, there's just nothing to
    # drift-check against.
    if not Path("WORKLOG.md").exists():
        print("check_worklog_drift: no local WORKLOG.md, skipping (nothing published to check)")
        return 0

    claimed = last_entry_verifier_state()
    if claimed is None:
        print("check_worklog_drift: no WORKLOG.md entry with a Verifier state: line", file=sys.stderr)
        return 1

    actual = actual_verify_summary()
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


if __name__ == "__main__":
    raise SystemExit(main())
