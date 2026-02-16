from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REQUIRED = ["commit-msg", "pre-commit"]


def main() -> int:
    hooks_path_result = subprocess.run(
        ["git", "config", "core.hooksPath"], capture_output=True, text=True, check=False
    )
    hooks_path = hooks_path_result.stdout.strip()
    if hooks_path != ".githooks":
        print(f"check_hooks: core.hooksPath is '{hooks_path}', expected '.githooks'", file=sys.stderr)
        return 1

    missing = [h for h in REQUIRED if not (Path(".githooks") / h).exists()]
    if missing:
        print(f"check_hooks: missing hook files: {missing}", file=sys.stderr)
        return 1

    not_executable = [
        h for h in REQUIRED if not (Path(".githooks") / h).stat().st_mode & 0o111
    ]
    if not_executable:
        print(f"check_hooks: hooks not executable: {not_executable}", file=sys.stderr)
        return 1

    print("check_hooks: hooksPath set, both hooks present and executable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
