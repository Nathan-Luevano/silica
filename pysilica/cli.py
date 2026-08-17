from __future__ import annotations

import sys
from pathlib import Path

import typer
import yaml

from pysilica import doctor as doctor_mod
from pysilica.analyze.rule_counts import generate_rule_counts
from pysilica.spec.compile import run_and_write
from pysilica.verify.registry import run_all
from pysilica.verify.types import VerifyResult

app = typer.Typer()

GOALS_FILE = Path("GOALS.yml")


def _write_goals_status(results: list[VerifyResult]) -> None:
    # GOALS.yml's status field is only ever written here, by an actual
    # verifier run - never by hand, per design.md §9. round-trips through
    # plain PyYAML rather than a comment-preserving library since the file
    # has none; sort_keys=False keeps each goal's field order stable.
    if not GOALS_FILE.exists():
        return
    doc = yaml.safe_load(GOALS_FILE.read_text())
    by_id = {r.goal_id: r for r in results}
    for goal in doc.get("goals", []):
        result = by_id.get(goal.get("id"))
        if result is not None:
            goal["status"] = "passing" if result.passed else "failing"
    GOALS_FILE.write_text(yaml.safe_dump(doc, sort_keys=False))


@app.command(name="compile-spec")
def compile_spec_cmd() -> None:
    result = run_and_write()
    for k, v in result.metrics.items():
        print(f"{k}: {v}")


@app.command(name="normalize-stats")
def normalize_stats_cmd() -> None:
    counts = generate_rule_counts()
    print("Generated normalization rule counts:", counts)


@app.command()
def doctor() -> None:
    checks = doctor_mod.run_all()
    failed = False
    for c in checks:
        status = "OK" if c.ok else "FAIL"
        if not c.ok:
            failed = True
        print(f"[{status}] {c.name}: {c.detail}")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def verify() -> None:
    results = run_all()
    failed = False
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        if not r.passed:
            failed = True
        print(f"[{status}] {r.goal_id}  evidence={r.evidence}  measured={r.measured}")
    _write_goals_status(results)
    if failed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    sys.exit(app())
