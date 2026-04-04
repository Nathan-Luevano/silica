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

ALL_RULES = [
    RULE_STRIP_COMMENTS,
    RULE_LOWERCASE,
    RULE_WHITESPACE,
    RULE_IMMEDIATES,
    RULE_ZERO_REGISTERS,
    RULE_SHIFT_DEFAULTS,
    RULE_CONDITIONS,
    RULE_MEMORY_OPERANDS,
]


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
    # only replace standalone w31/x31 operand tokens, not inside sp
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
