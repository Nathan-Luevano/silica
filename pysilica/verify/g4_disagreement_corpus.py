from __future__ import annotations

from pathlib import Path

from pysilica.verify.types import VerifyResult

CORPUS_DIR = Path("artifacts/disagreements")
TAXONOMY = {"VALIDITY", "MNEMONIC", "OPERAND", "ALIAS", "FORMATTING", "NORMALIZATION_UNCERTAIN", "CRASH"}


def verify_g4_disagreement_corpus() -> VerifyResult:
    if not CORPUS_DIR.is_dir() or not any(CORPUS_DIR.glob("*.zst")):
        return VerifyResult("G4", False, {"missing": str(CORPUS_DIR)}, {})
    return VerifyResult(
        "G4", False,
        {"reason": "taxonomy classification coverage not yet verified"},
        {"expected_taxonomy": sorted(TAXONOMY)},
    )
