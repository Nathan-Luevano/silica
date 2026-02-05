from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from pysilica.verify.g1_spec_oracle import verify_g1_spec_oracle
from pysilica.verify.g2_exhaustive_coverage import verify_g2_exhaustive_coverage
from pysilica.verify.g3_normalization import verify_g3_normalization
from pysilica.verify.g4_disagreement_corpus import verify_g4_disagreement_corpus
from pysilica.verify.g5_published_metrics import verify_g5_published_metrics
from pysilica.verify.g6_reproducers import verify_g6_reproducers
from pysilica.verify.g7_reproducibility import verify_g7_reproducibility
from pysilica.verify.types import VerifyResult

REGISTRY: dict[str, tuple[Callable[[], VerifyResult], str]] = {
    "G1": (verify_g1_spec_oracle, "pysilica/verify/g1_spec_oracle.py"),
    "G2": (verify_g2_exhaustive_coverage, "pysilica/verify/g2_exhaustive_coverage.py"),
    "G3": (verify_g3_normalization, "pysilica/verify/g3_normalization.py"),
    "G4": (verify_g4_disagreement_corpus, "pysilica/verify/g4_disagreement_corpus.py"),
    "G5": (verify_g5_published_metrics, "pysilica/verify/g5_published_metrics.py"),
    "G6": (verify_g6_reproducers, "pysilica/verify/g6_reproducers.py"),
    "G7": (verify_g7_reproducibility, "pysilica/verify/g7_reproducibility.py"),
}


def verifier_sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_all() -> list[VerifyResult]:
    return [fn() for fn, _ in REGISTRY.values()]
