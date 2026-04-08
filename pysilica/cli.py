from __future__ import annotations

import sys

import typer

from pysilica import doctor as doctor_mod
from pysilica.analyze.rule_counts import generate_rule_counts
from pysilica.spec.compile import run_and_write
from pysilica.verify.registry import run_all

app = typer.Typer()


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
    if failed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    sys.exit(app())
