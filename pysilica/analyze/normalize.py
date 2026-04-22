from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

RULE_STRIP_COMMENTS = "strip_comments"
RULE_LOWERCASE = "lowercase"
RULE_WHITESPACE = "whitespace"
RULE_IMMEDIATES = "immediates"
RULE_ZERO_REGISTERS = "zero_registers"
RULE_SHIFT_DEFAULTS = "shift_defaults"
RULE_CONDITIONS = "condition_codes"
RULE_MEMORY_OPERANDS = "memory_operands"
RULE_SPEC_ALIAS = "spec_alias"

ALL_RULES = [
    RULE_STRIP_COMMENTS,
    RULE_LOWERCASE,
    RULE_WHITESPACE,
    RULE_IMMEDIATES,
    RULE_ZERO_REGISTERS,
    RULE_SHIFT_DEFAULTS,
    RULE_CONDITIONS,
    RULE_MEMORY_OPERANDS,
    RULE_SPEC_ALIAS,
]

TAXONOMY_EQUIVALENT = "EQUIVALENT"
TAXONOMY_VALIDITY = "VALIDITY"
TAXONOMY_MNEMONIC = "MNEMONIC"
TAXONOMY_OPERAND = "OPERAND"
TAXONOMY_ALIAS = "ALIAS"
TAXONOMY_FORMATTING = "FORMATTING"
TAXONOMY_NORMALIZATION_UNCERTAIN = "NORMALIZATION_UNCERTAIN"
TAXONOMY_CRASH = "CRASH"


def strip_comments(text: str) -> tuple[str, bool]:
    # handles both // and ; comment styles across capstone/llvm/objdump
    cleaned = re.sub(r"(//|;).*$", "", text).strip()
    return cleaned, cleaned != text


def normalize_case(text: str) -> tuple[str, bool]:
    lowered = text.lower()
    return lowered, lowered != text


def normalize_whitespace(text: str) -> tuple[str, bool]:
    # collapse consecutive whitespace and normalize commas/brackets
    collapsed = re.sub(r"\s+", " ", text.strip())
    collapsed = re.sub(r"\s*,\s*", ", ", collapsed)
    collapsed = re.sub(r"\[\s+", "[", collapsed)
    collapsed = re.sub(r"\s+\]", "]", collapsed)
    collapsed = re.sub(r",\s*\]", "]", collapsed)
    return collapsed, collapsed != text


def normalize_immediates(text: str) -> tuple[str, bool]:
    # standardizes hex immediates #0x... to canonical decimal #...
    def repl_hex(match: re.Match[str]) -> str:
        prefix = match.group(1)
        sign = match.group(2) or ""
        hex_val = match.group(3)
        val = int(hex_val, 16)
        if sign == "-":
            val = -val
        return f"{prefix}#{val}"

    # matches #0x10, #-0x10, # 0x10
    res = re.sub(r"(^|[,\s\[])#\s*(-?)0x([0-9a-fA-F]+)", repl_hex, text)
    # also strip space between # and number e.g. # 16 -> #16
    res = re.sub(r"#\s+(\d+)", r"#\1", res)
    return res, res != text


def normalize_zero_registers(text: str) -> tuple[str, bool]:
    # wzr/xzr canonicalization when disassemblers output w31/x31 in non-sp context
    res = re.sub(r"\b(w|x)31\b", r"\g<1>zr", text)
    return res, res != text


def normalize_shift_defaults(text: str) -> tuple[str, bool]:
    # standardizes implicit default shift amount lsl #0
    res = re.sub(r",\s*lsl\s+#0\b", "", text)
    return res, res != text


def normalize_conditions(text: str) -> tuple[str, bool]:
    # hs/cs and lo/cc are exact synonyms in arm condition codes
    res = re.sub(r"\bb\.hs\b", "b.cs", text)
    res = re.sub(r"\bb\.lo\b", "b.cc", res)
    return res, res != text


def normalize_memory_operands(text: str) -> tuple[str, bool]:
    # standardizes [reg, #0] to [reg]
    res = re.sub(r"\[([a-zA-Z0-9_]+),\s*#0\]", r"[\1]", text)
    return res, res != text


def normalize_spec_alias(text: str) -> tuple[str, bool]:
    # spec-driven alias canonicalization from ARM ISA <alias_list>
    res = text
    # orr Rd, wzr/xzr, Rm / orr Rd, Rm, wzr/xzr -> mov Rd, Rm
    res = re.sub(r"\borr\s+([wx]\d+|[wx]zr),\s*[wx]zr,\s*([wx]\d+|[wx]zr)\b", r"mov \1, \2", res)
    res = re.sub(r"\borr\s+([wx]\d+|[wx]zr),\s*([wx]\d+|[wx]zr),\s*[wx]zr\b", r"mov \1, \2", res)
    # subs wzr/xzr, Rn, Rm / #imm -> cmp Rn, Rm / #imm
    res = re.sub(r"\bsubs\s+[wx]zr,\s*([wx]\d+),\s*([^,\s]+)\b", r"cmp \1, \2", res)
    # adds wzr/xzr, Rn, Rm / #imm -> cmn Rn, Rm / #imm
    res = re.sub(r"\badds\s+[wx]zr,\s*([wx]\d+),\s*([^,\s]+)\b", r"cmn \1, \2", res)
    # ands wzr/xzr, Rn, Rm / #imm -> tst Rn, Rm / #imm
    res = re.sub(r"\bands\s+[wx]zr,\s*([wx]\d+),\s*([^,\s]+)\b", r"tst \1, \2", res)
    # orn Rd, wzr/xzr, Rm -> mvn Rd, Rm
    res = re.sub(r"\born\s+([wx]\d+|[wx]zr),\s*[wx]zr,\s*([wx]\d+|[wx]zr)\b", r"mvn \1, \2", res)
    # sub Rd, wzr/xzr, Rm -> neg Rd, Rm (where Rd is not wzr/xzr)
    res = re.sub(r"\bsub\s+([wx]\d+),\s*[wx]zr,\s*([wx]\d+|[wx]zr)\b", r"neg \1, \2", res)
    # subs Rd, wzr/xzr, Rm -> negs Rd, Rm (where Rd is not wzr/xzr)
    res = re.sub(r"\bsubs\s+([wx]\d+),\s*[wx]zr,\s*([wx]\d+|[wx]zr)\b", r"negs \1, \2", res)
    return res, res != text


def classify_disagreement(
    raw_a: str | None,
    raw_b: str | None,
    norm_a: str | None,
    norm_b: str | None,
) -> str:
    if raw_a is None or raw_b is None:
        return TAXONOMY_VALIDITY
    if norm_a == norm_b:
        return TAXONOMY_EQUIVALENT
    tokens_a = norm_a.split() if norm_a else []
    tokens_b = norm_b.split() if norm_b else []
    mnem_a = tokens_a[0].lower() if tokens_a else ""
    mnem_b = tokens_b[0].lower() if tokens_b else ""
    if mnem_a == mnem_b:
        return TAXONOMY_OPERAND
    return TAXONOMY_NORMALIZATION_UNCERTAIN


@dataclass
class NormalizationResult:
    original: str
    normalized: str
    applied_rules: list[str] = field(default_factory=list)


class Normalizer:
    def normalize(self, text: str) -> NormalizationResult:
        applied: list[str] = []
        curr = text

        s, modified = strip_comments(curr)
        if modified:
            applied.append(RULE_STRIP_COMMENTS)
            curr = s

        s, modified = normalize_case(curr)
        if modified:
            applied.append(RULE_LOWERCASE)
            curr = s

        s, modified = normalize_whitespace(curr)
        if modified:
            applied.append(RULE_WHITESPACE)
            curr = s

        s, modified = normalize_immediates(curr)
        if modified:
            applied.append(RULE_IMMEDIATES)
            curr = s

        s, modified = normalize_zero_registers(curr)
        if modified:
            applied.append(RULE_ZERO_REGISTERS)
            curr = s

        s, modified = normalize_shift_defaults(curr)
        if modified:
            applied.append(RULE_SHIFT_DEFAULTS)
            curr = s

        s, modified = normalize_conditions(curr)
        if modified:
            applied.append(RULE_CONDITIONS)
            curr = s

        s, modified = normalize_memory_operands(curr)
        if modified:
            applied.append(RULE_MEMORY_OPERANDS)
            curr = s

        s, modified = normalize_spec_alias(curr)
        if modified:
            applied.append(RULE_SPEC_ALIAS)
            curr = s

        return NormalizationResult(
            original=text,
            normalized=curr,
            applied_rules=applied,
        )


class RuleTracker:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {rule: 0 for rule in ALL_RULES}
        self._total_comparisons: int = 0
        self._collapsed_disagreements: int = 0

    def record(self, applied_rules: list[str], collapsed: bool = False) -> None:
        self._total_comparisons += 1
        if collapsed:
            self._collapsed_disagreements += 1
        for rule in applied_rules:
            if rule in self._counts:
                self._counts[rule] += 1

    def counts(self) -> dict[str, int]:
        return dict(self._counts)

    def to_dict(self) -> dict[str, object]:
        return {
            "total_comparisons": self._total_comparisons,
            "collapsed_disagreements": self._collapsed_disagreements,
            "rule_counts": dict(self._counts),
        }

    def write_json(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))
