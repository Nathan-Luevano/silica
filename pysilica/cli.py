from __future__ import annotations

import sys

import typer

from pysilica import doctor as doctor_mod
from pysilica.verify.registry import run_all

app = typer.Typer()


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
