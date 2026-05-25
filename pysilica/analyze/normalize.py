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

SPEC_ALIASES_DEFAULT_PATH = Path("artifacts/spec_aliases.json")


def load_spec_aliases(path: Path | str = SPEC_ALIASES_DEFAULT_PATH) -> dict[str, list[dict[str, str]]]:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text())  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError):
            pass
    # fallback in-scope aliases derived from spec <alias_list>, in the same
    # shape (including "condition") the real artifacts/spec_aliases.json
    # uses, since normalize_spec_alias's generic engine dispatches on it
    return {
        "ORR": [{"alias_mnemonic": "MOV", "condition": "Rn == '11111'"}],
        "SUBS": [
            {"alias_mnemonic": "CMP", "condition": "Rd == '11111'"},
            {"alias_mnemonic": "NEGS", "condition": "Rn == '11111' && Rd != '11111'"},
        ],
        "ADDS": [{"alias_mnemonic": "CMN", "condition": "Rd == '11111'"}],
        "ANDS": [{"alias_mnemonic": "TST", "condition": "Rd == '11111'"}],
        "ORN": [{"alias_mnemonic": "MVN", "condition": "Rn == '11111'"}],
        "SUB": [{"alias_mnemonic": "NEG", "condition": "Rn == '11111'"}],
        "ASRV": [{"alias_mnemonic": "ASR", "condition": "Unconditionally"}],
        "LSLV": [{"alias_mnemonic": "LSL", "condition": "Unconditionally"}],
        "LSRV": [{"alias_mnemonic": "LSR", "condition": "Unconditionally"}],
        "RORV": [{"alias_mnemonic": "ROR", "condition": "Unconditionally"}],
        "EXTR": [{"alias_mnemonic": "ROR", "condition": "Rn == Rm"}],
    }


def strip_comments(text: str) -> tuple[str, bool]:
    cleaned = re.sub(r"(//|;).*$", "", text).strip()
    return cleaned, cleaned != text


def normalize_case(text: str) -> tuple[str, bool]:
    lowered = text.lower()
    return lowered, lowered != text


def normalize_whitespace(text: str) -> tuple[str, bool]:
    collapsed = re.sub(r"\s+", " ", text.strip())
    collapsed = re.sub(r"\s*,\s*", ", ", collapsed)
    collapsed = re.sub(r"\[\s+", "[", collapsed)
    collapsed = re.sub(r"\s+\]", "]", collapsed)
    collapsed = re.sub(r",\s*\]", "]", collapsed)
    return collapsed, collapsed != text


def normalize_immediates(text: str) -> tuple[str, bool]:
    def repl_hex(match: re.Match[str]) -> str:
        prefix = match.group(1)
        sign = match.group(2) or ""
        hex_val = match.group(3)
        val = int(hex_val, 16)
        if sign == "-":
            val = -val
        return f"{prefix}#{val}"

    res = re.sub(r"(^|[,\s\[])#\s*(-?)0x([0-9a-fA-F]+)", repl_hex, text)
    res = re.sub(r"#\s+(\d+)", r"#\1", res)
    return res, res != text


def normalize_zero_registers(text: str) -> tuple[str, bool]:
    res = re.sub(r"\b(w|x)31\b", r"\g<1>zr", text)
    return res, res != text


def normalize_shift_defaults(text: str) -> tuple[str, bool]:
    res = re.sub(r",\s*lsl\s+#0\b", "", text)
    return res, res != text


def normalize_conditions(text: str) -> tuple[str, bool]:
    res = re.sub(r"\bb\.hs\b", "b.cs", text)
    res = re.sub(r"\bb\.lo\b", "b.cc", res)
    return res, res != text


def normalize_memory_operands(text: str) -> tuple[str, bool]:
    res = re.sub(r"\[([a-zA-Z0-9_]+),\s*#0\]", r"[\1]", text)
    return res, res != text


# register-field -> conventional operand position for the ALU/atomic/CSINC-
# style forms this generic engine understands. Not exhaustive (system-register
# and SIMD forms use different conventions), which is fine: an alias whose
# condition mentions a field not in this map is conservatively skipped below.
_FIELD_POSITION = {"Rd": 0, "Rn": 1, "Rm": 2, "Ra": 3, "Rt": 1}

_ZR_OPERAND_RE = re.compile(r"^[wx]zr$")
_REG_OPERAND_RE = re.compile(r"^[wx]\d{1,2}$")
_FIELD_ZR_EQ_RE = re.compile(r"\b(R\w*)\s*==\s*'11111'")
_FIELD_NOT_ZR_RE = re.compile(r"\b(R\w*)\s*!=\s*'11111'")
_FIELD_EQ_FIELD_RE = re.compile(r"\b(R\w*)\s*==\s*(R\w*)\b")


def _clean_alias_mnemonic(raw: str) -> str:
    # the spec XML sometimes lists a comma-joined pair like "STADD, STADDL"
    # for one page; alias_mnemonic then arrives as "STADD," -- take the first
    return raw.split(",")[0].strip()


def _split_top_level_operands(rest: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur = ""
    for ch in rest:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _positions_for_fields(fields: list[str]) -> list[int] | None:
    positions = []
    for f in fields:
        pos = _FIELD_POSITION.get(f)
        if pos is None:
            return None
        positions.append(pos)
    return sorted(set(positions))


def _violates_not_zr_guard(condition: str, operands: list[str]) -> bool:
    for fname in _FIELD_NOT_ZR_RE.findall(condition):
        pos = _FIELD_POSITION.get(fname)
        if pos is not None and pos < len(operands) and _ZR_OPERAND_RE.match(operands[pos]):
            return True
    return False


def _apply_one_alias(text: str, old_mnem: str, new_mnem: str, condition: str) -> str | None:
    old = old_mnem.lower()
    new = _clean_alias_mnemonic(new_mnem).lower()
    if not new:
        return None
    m = re.match(rf"^{re.escape(old)}\b(.*)$", text)
    if not m:
        return None
    rest = m.group(1).strip()
    operands = _split_top_level_operands(rest) if rest else []

    def render(kept: list[str]) -> str:
        return f"{new} {', '.join(kept)}".strip() if kept else new

    cond = condition.strip()
    if cond == "Unconditionally":
        return render(operands)

    if _violates_not_zr_guard(cond, operands):
        return None

    zr_fields = _FIELD_ZR_EQ_RE.findall(cond)
    if zr_fields:
        positions = _positions_for_fields(zr_fields)
        if not positions or any(p >= len(operands) for p in positions):
            return None
        if not all(_ZR_OPERAND_RE.match(operands[p]) for p in positions):
            return None
        return render([o for i, o in enumerate(operands) if i not in positions])

    eq_match = _FIELD_EQ_FIELD_RE.search(cond)
    if eq_match:
        positions = _positions_for_fields([eq_match.group(1), eq_match.group(2)])
        if not positions or len(positions) != 2 or any(p >= len(operands) for p in positions):
            return None
        lo, hi = positions
        if not (_REG_OPERAND_RE.match(operands[lo]) and operands[lo] == operands[hi]):
            return None
        return render([o for i, o in enumerate(operands) if i != hi])

    return None


def normalize_spec_alias(
    text: str,
    spec_aliases: dict[str, list[dict[str, str]]] | None = None,
) -> tuple[str, bool]:
    # spec-driven alias canonicalization: walks every base mnemonic and every
    # alias listed for it in the spec's <alias_list> (loaded generically, not
    # a hand-picked subset), and applies the first alias whose condition can
    # be verified purely from the disassembly text (an "Unconditionally"
    # alias, a specific field forced to the zero register, or two fields
    # required equal). Conditions that need bitfield/immediate values not
    # visible in normalized text (UInt(imms) < UInt(immr), SysOp(...), etc.)
    # are left alone -- conservative per design.md sec7, not a gap.
    if spec_aliases is None:
        spec_aliases = load_spec_aliases()

    res = text
    for base_mnem, aliases in spec_aliases.items():
        for a in aliases:
            alias_mnem = a.get("alias_mnemonic", "")
            condition = a.get("condition", "")
            out = _apply_one_alias(res, base_mnem, alias_mnem, condition)
            if out is not None:
                res = out
                break

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
    def __init__(self, spec_aliases: dict[str, list[dict[str, str]]] | None = None) -> None:
        self.spec_aliases = spec_aliases if spec_aliases is not None else load_spec_aliases()

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

        s, modified = normalize_spec_alias(curr, self.spec_aliases)
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
