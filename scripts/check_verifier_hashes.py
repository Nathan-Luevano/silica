from __future__ import annotations

import sys
from pathlib import Path

import yaml

from pysilica.verify.registry import verifier_sha256


def main() -> int:
    doc = yaml.safe_load(Path("GOALS.yml").read_text())
    mismatched = []
    for goal in doc.get("goals", []):
        path = goal["verifier_file"]
        recorded = goal["verifier_sha256"]
        actual = verifier_sha256(path)
        if actual != recorded:
            mismatched.append((goal["id"], path, recorded, actual))

    if mismatched:
        for goal_id, path, recorded, actual in mismatched:
            print(
                f"check_verifier_hashes: {goal_id} ({path}) hash mismatch - "
                f"GOALS.yml says {recorded}, file is actually {actual}. "
                f"if this is a deliberate strengthening, update GOALS.yml in a "
                f"test:-typed commit stating why (design.md §9.1).",
                file=sys.stderr,
            )
        return 1

    print(f"check_verifier_hashes: all {len(doc.get('goals', []))} verifier hashes match GOALS.yml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
