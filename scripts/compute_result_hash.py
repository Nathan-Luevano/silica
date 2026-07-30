from __future__ import annotations

import hashlib
from pathlib import Path

# order and inputs must match docs/formats.md's "artifacts/result_hash.txt"
# section and pysilica/verify/g7_reproducibility.py's _compute_result_hash
# exactly - this is the reference implementation the verifier re-derives.
DECODE_TABLE = Path("artifacts/decode-table.bin")
BITMAPS_DIR = Path("artifacts/bitmaps")
BITMAP_ORDER = ("capstone", "llvm", "spec", "unicorn")
G4_METRICS = Path("artifacts/g4_metrics.json")
G5_REPORT = Path("artifacts/report/metrics.json")
REPRODUCERS_DIR = Path("artifacts/reproducers")
OUT = Path("artifacts/result_hash.txt")


def compute() -> str:
    h = hashlib.sha256()
    h.update(DECODE_TABLE.read_bytes())
    for name in BITMAP_ORDER:
        h.update((BITMAPS_DIR / f"{name}.bin").read_bytes())
    h.update(G4_METRICS.read_bytes())
    h.update(G5_REPORT.read_bytes())
    for p in sorted(REPRODUCERS_DIR.glob("*.md")):
        h.update(p.read_bytes())
    return h.hexdigest()


def main() -> None:
    digest = compute()
    OUT.write_text(digest)
    print(digest)


if __name__ == "__main__":
    main()
