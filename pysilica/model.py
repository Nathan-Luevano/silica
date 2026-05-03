from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Allocation(Enum):
    ALLOCATED = "allocated"
    UNALLOCATED = "unallocated"


@dataclass(frozen=True)
class Box:
    hibit: int
    width: int
    name: str | None
    fixed_bits: str | None  # e.g. "110", None when unconstrained


@dataclass(frozen=True)
class InstructionForm:
    iclass_id: str
    encoding_name: str
    psname: str
    mnemonic: str
    boxes: tuple[Box, ...]
    gating_features: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AliasRef:
    base_mnemonic: str
    alias_mnemonic: str
    alias_name: str
    alias_page_id: str
    alias_file: str
    condition: str


@dataclass(frozen=True)
class SpecDecodeResult:
    word: int
    allocation: Allocation
    form: InstructionForm | None
