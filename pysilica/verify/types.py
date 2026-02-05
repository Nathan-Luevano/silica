from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VerifyResult:
    goal_id: str
    passed: bool
    evidence: dict[str, Any]
    measured: dict[str, Any]
