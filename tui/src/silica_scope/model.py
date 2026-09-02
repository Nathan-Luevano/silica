from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOTAL_WORDS = 1 << 32
SHARD_COUNT = 256
SHARD_SIZE = 1 << 24

TAXONOMY = (
    "VALIDITY",
    "MNEMONIC",
    "OPERAND",
    "ALIAS",
    "FORMATTING",
    "NORMALIZATION_UNCERTAIN",
    "CRASH",
)

ORACLES = ("capstone", "llvm", "spec", "unicorn")
TOOLS = ("capstone", "llvm", "unicorn")


class LoadError(Exception):
    pass


@dataclass
class Loaded:
    # every loader returns one of these so a corrupt file degrades one panel
    # instead of taking the whole app down.
    value: Any = None
    error: str = ""
    path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.error == "" and self.value is not None


def _read_json(path: Path) -> Loaded:
    if not path.is_file():
        return Loaded(None, f"not found: {path}", path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Loaded(None, f"unreadable: {exc}", path)
    if not raw.strip():
        return Loaded(None, "file is empty", path)
    try:
        return Loaded(json.loads(raw), "", path)
    except json.JSONDecodeError as exc:
        return Loaded(None, f"invalid JSON at line {exc.lineno}, col {exc.colno}: {exc.msg}", path)


@dataclass
class ToolMetrics:
    name: str
    validity_disagreements: int
    validity_agreement_micro: float
    macro_validity_agreement: float
    text_disagreements: int
    text_agreement_micro: float
    text_method: str
    text_sample_size: int
    text_population: int


@dataclass
class Metrics:
    format_version: int
    total_words: int
    spec_valid_count: int
    per_tool: dict[str, ToolMetrics]
    ranking_worst_first: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def spec_invalid_count(self) -> int:
        return max(self.total_words - self.spec_valid_count, 0)

    def evidence_problems(self) -> list[str]:
        problems = list(self.warnings)
        if self.format_version != 1:
            problems.append(f"format_version is {self.format_version}, expected 1")
        if self.total_words != TOTAL_WORDS:
            problems.append(f"total_words is {self.total_words:,}, expected {TOTAL_WORDS:,}")
        if not 0 <= self.spec_valid_count <= TOTAL_WORDS:
            problems.append(f"spec_valid_count is outside 0..{TOTAL_WORDS}")
        if set(self.per_tool) != set(TOOLS):
            problems.append(f"per_tool keys must be exactly {list(TOOLS)!r}")
        if self.ranking_worst_first != sorted(
            self.per_tool, key=lambda name: self.per_tool[name].validity_agreement_micro
        ):
            problems.append("tool_ranking_worst_first does not match the reported agreement rates")
        if len(self.ranking_worst_first) != len(set(self.ranking_worst_first)):
            problems.append("tool_ranking_worst_first contains duplicates")
        if set(self.ranking_worst_first) != set(TOOLS):
            problems.append(f"tool_ranking_worst_first must contain exactly {list(TOOLS)!r}")
        for name in TOOLS:
            tool = self.per_tool.get(name)
            if tool is not None:
                problems.extend(_tool_metric_problems(tool, self.total_words))
        present = [self.per_tool[name] for name in TOOLS if name in self.per_tool]
        for label, values in (
            ("text_tier_method", {tool.text_method for tool in present}),
            ("text_tier_sample_size", {tool.text_sample_size for tool in present}),
            ("text_tier_population", {tool.text_population for tool in present}),
        ):
            if len(values) > 1:
                problems.append(f"per-tool {label} values disagree")
        return _dedupe(problems)

    @property
    def supports_sweep_evidence(self) -> bool:
        return not self.evidence_problems()


def _int_num(
    d: dict[str, Any], key: str, warnings: list[str], where: str, default: int = 0
) -> int:
    v = d.get(key, None)
    if v is None:
        warnings.append(f"{where}: missing '{key}'")
        return default
    if not isinstance(v, int) or isinstance(v, bool):
        warnings.append(f"{where}: '{key}' is not an integer ({type(v).__name__})")
        return default
    return v


def _float_num(
    d: dict[str, Any], key: str, warnings: list[str], where: str, default: float = 0.0
) -> float:
    v = d.get(key, None)
    if v is None:
        warnings.append(f"{where}: missing '{key}'")
        return default
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        warnings.append(f"{where}: '{key}' is not a finite number ({type(v).__name__})")
        return default
    return float(v)


def _tool_metric_problems(tool: ToolMetrics, total_words: int) -> list[str]:
    where = f"per_tool.{tool.name}"
    problems: list[str] = []
    if not 0 <= tool.validity_disagreements <= TOTAL_WORDS:
        problems.append(f"{where}: validity disagreements are outside 0..{TOTAL_WORDS}")
    for label, value in (
        ("validity_agreement_micro", tool.validity_agreement_micro),
        ("macro_validity_agreement", tool.macro_validity_agreement),
        ("text_tier_agreement_micro", tool.text_agreement_micro),
    ):
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            problems.append(f"{where}: {label} is outside 0..1")
    if total_words == TOTAL_WORDS and 0 <= tool.validity_disagreements <= TOTAL_WORDS:
        expected = (TOTAL_WORDS - tool.validity_disagreements) / TOTAL_WORDS
        if not math.isclose(tool.validity_agreement_micro, expected, rel_tol=0.0, abs_tol=1e-15):
            problems.append(f"{where}: validity_agreement_micro contradicts its disagreement count")
    if tool.text_method not in {"exhaustive", "sampled"}:
        problems.append(f"{where}: text_tier_method must be 'exhaustive' or 'sampled'")
    for label, value in (
        ("text_tier_disagreements_with_spec", tool.text_disagreements),
        ("text_tier_sample_size", tool.text_sample_size),
        ("text_tier_population", tool.text_population),
    ):
        if value < 0:
            problems.append(f"{where}: {label} must be nonnegative")
    if tool.text_sample_size > tool.text_population:
        problems.append(f"{where}: text_tier_sample_size exceeds text_tier_population")
    if tool.text_disagreements > tool.text_population:
        problems.append(f"{where}: text disagreements exceed text_tier_population")
    if tool.text_population > 0 and 0 <= tool.text_disagreements <= tool.text_population:
        expected = (tool.text_population - tool.text_disagreements) / tool.text_population
        if not math.isclose(tool.text_agreement_micro, expected, rel_tol=0.0, abs_tol=1e-15):
            problems.append(f"{where}: text_tier_agreement_micro contradicts its counts")
    return problems


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def load_metrics(path: Path) -> Loaded:
    loaded = _read_json(path)
    if not loaded.ok:
        return loaded
    data = loaded.value
    if not isinstance(data, dict):
        return Loaded(None, "expected a JSON object at the top level", path)
    warnings: list[str] = []
    per_tool: dict[str, ToolMetrics] = {}
    raw_tools = data.get("per_tool")
    if not isinstance(raw_tools, dict):
        warnings.append("missing or malformed 'per_tool'")
        raw_tools = {}
    for name, blob in raw_tools.items():
        if not isinstance(blob, dict):
            warnings.append(f"per_tool.{name}: not an object")
            continue
        where = f"per_tool.{name}"
        per_tool[name] = ToolMetrics(
            name=name,
            validity_disagreements=_int_num(
                blob, "validity_disagreements_with_spec", warnings, where
            ),
            validity_agreement_micro=_float_num(blob, "validity_agreement_micro", warnings, where),
            macro_validity_agreement=_float_num(blob, "macro_validity_agreement", warnings, where),
            text_disagreements=_int_num(blob, "text_tier_disagreements_with_spec", warnings, where),
            text_agreement_micro=_float_num(blob, "text_tier_agreement_micro", warnings, where),
            text_method=str(blob.get("text_tier_method", "unknown")),
            text_sample_size=_int_num(blob, "text_tier_sample_size", warnings, where),
            text_population=_int_num(blob, "text_tier_population", warnings, where),
        )
    ranking = data.get("tool_ranking_worst_first")
    if not isinstance(ranking, list) or not all(isinstance(x, str) for x in ranking):
        warnings.append("missing or malformed 'tool_ranking_worst_first'")
        ranking = sorted(per_tool, key=lambda n: per_tool[n].validity_agreement_micro)
    total = _int_num(data, "total_words", warnings, "root", TOTAL_WORDS)
    return Loaded(
        Metrics(
            format_version=_int_num(data, "format_version", warnings, "root"),
            total_words=total,
            spec_valid_count=_int_num(data, "spec_valid_count", warnings, "root"),
            per_tool=per_tool,
            ranking_worst_first=list(ranking),
            warnings=warnings,
        ),
        "",
        path,
    )


def g1_evidence_problems(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["expected a JSON object at the top level"]
    problems: list[str] = []
    release = value.get("spec_release")
    if not isinstance(release, str) or not release.strip():
        problems.append("missing or malformed 'spec_release'")
    checked = _g1_int(value, "tiling_files_checked", problems)
    passed = _g1_int(value, "tiling_files_passed", problems)
    allocated = _g1_int(value, "allocated", problems)
    unallocated = _g1_int(value, "unallocated", problems)
    if checked is not None and checked <= 0:
        problems.append("tiling_files_checked must be positive")
    if checked is not None and passed is not None and passed != checked:
        problems.append("tiling_files_passed does not equal tiling_files_checked")
    if allocated is not None and not 0 <= allocated <= TOTAL_WORDS:
        problems.append(f"allocated is outside 0..{TOTAL_WORDS}")
    if unallocated is not None and not 0 <= unallocated <= TOTAL_WORDS:
        problems.append(f"unallocated is outside 0..{TOTAL_WORDS}")
    if allocated is not None and unallocated is not None and allocated + unallocated != TOTAL_WORDS:
        problems.append(f"allocated + unallocated does not equal {TOTAL_WORDS}")
    if value.get("ret_test_word") != "0xd65f03c0":
        problems.append("ret_test_word is not 0xd65f03c0")
    if value.get("ret_test_passed") is not True:
        problems.append("ret_test_passed is not true")
    return problems


def _g1_int(value: dict[str, Any], key: str, problems: list[str]) -> int | None:
    raw = value.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool):
        problems.append(f"missing or malformed '{key}'")
        return None
    return raw


@dataclass
class Shard:
    shard_id: int
    start: int
    end: int
    valid_counts: dict[str, int]
    crash_count: int
    untriaged_crash_count: int
    content_hash: str
    duration_ms: int
    status: str
    disagreement_bytes: int = 0
    has_corpus: bool = False

    @property
    def size(self) -> int:
        return max(self.end - self.start, 1)

    @property
    def label(self) -> str:
        return f"{self.shard_id:03d}"

    def spread(self) -> float:
        # how far apart the oracles are inside this shard, 0..1 - the map's
        # colour channel. max minus min valid count over the shard size.
        if not self.valid_counts:
            return 0.0
        counts = list(self.valid_counts.values())
        return (max(counts) - min(counts)) / self.size


def load_shards(directory: Path) -> tuple[list[Shard], list[str]]:
    shards: list[Shard] = []
    problems: list[str] = []
    if not directory.is_dir():
        return shards, [f"not found: {directory}"]
    seen_ids: set[int] = set()
    for path in sorted(directory.glob("*.json")):
        loaded = _read_json(path)
        if not loaded.ok:
            problems.append(f"{path.name}: {loaded.error}")
            continue
        d = loaded.value
        if not isinstance(d, dict):
            problems.append(f"{path.name}: not an object")
            continue
        shard, record_problems = _parse_shard(path, d, seen_ids)
        problems.extend(f"{path.name}: {problem}" for problem in record_problems)
        if shard is not None:
            shards.append(shard)
            seen_ids.add(shard.shard_id)
    shards.sort(key=lambda s: s.shard_id)
    return shards, problems


def _strict_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an integer")
    return value


def _parse_shard(
    path: Path, d: dict[str, Any], seen_ids: set[int]
) -> tuple[Shard | None, list[str]]:
    problems: list[str] = []
    try:
        shard_id = _strict_int(d.get("shard_id"), "shard_id")
    except TypeError as exc:
        return None, [str(exc)]

    if not 0 <= shard_id < SHARD_COUNT:
        problems.append(f"shard_id {shard_id} is outside 0..255")
    expected_name = f"{shard_id:03d}.json"
    if path.name != expected_name:
        problems.append(f"filename does not match shard_id {shard_id} (expected {expected_name})")
    if shard_id in seen_ids:
        problems.append(f"duplicate shard_id {shard_id}")

    start = _checked_int(d, "start", problems)
    end = _checked_int(d, "end", problems)
    expected_start = shard_id * SHARD_SIZE
    expected_end = expected_start + SHARD_SIZE
    if start is not None and start != expected_start:
        problems.append(f"start is {start}, expected {expected_start}")
    if end is not None and end != expected_end:
        problems.append(f"end is {end}, expected {expected_end}")

    raw_oracles = d.get("oracles")
    if raw_oracles != list(ORACLES):
        problems.append(f"oracles must be {list(ORACLES)!r} in that order")

    counts = _checked_counts(d.get("valid_counts"), problems)
    crash_count = _checked_nonnegative(d, "crash_count", problems)
    untriaged = _checked_nonnegative(d, "untriaged_crash_count", problems)
    if crash_count is not None and untriaged is not None and untriaged > crash_count:
        problems.append("untriaged_crash_count exceeds crash_count")

    content_hash = d.get("content_hash")
    if not isinstance(content_hash, str) or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
        problems.append("content_hash is not a 64-char lowercase sha256 hex digest")
    duration_ms = _checked_nonnegative(d, "duration_ms", problems)
    status = d.get("status")
    if status not in {"complete", "crashed"}:
        problems.append("status must be 'complete' or 'crashed'")
    if status == "complete" and untriaged not in {None, 0}:
        problems.append("complete shard has untriaged crashes")

    if problems:
        return None, problems
    assert start is not None
    assert end is not None
    assert counts is not None
    assert crash_count is not None
    assert untriaged is not None
    assert isinstance(content_hash, str)
    assert duration_ms is not None
    assert isinstance(status, str)
    return (
        Shard(
            shard_id=shard_id,
            start=start,
            end=end,
            valid_counts=counts,
            crash_count=crash_count,
            untriaged_crash_count=untriaged,
            content_hash=content_hash,
            duration_ms=duration_ms,
            status=status,
        ),
        [],
    )


def _checked_int(d: dict[str, Any], key: str, problems: list[str]) -> int | None:
    try:
        return _strict_int(d.get(key), key)
    except TypeError as exc:
        problems.append(str(exc))
        return None


def _checked_nonnegative(d: dict[str, Any], key: str, problems: list[str]) -> int | None:
    value = _checked_int(d, key, problems)
    if value is not None and value < 0:
        problems.append(f"{key} must be nonnegative")
    return value


def _checked_counts(value: object, problems: list[str]) -> dict[str, int] | None:
    if not isinstance(value, dict):
        problems.append("valid_counts must be an object")
        return None
    if set(value) != set(ORACLES):
        problems.append(f"valid_counts keys must be exactly {list(ORACLES)!r}")
        return None
    counts: dict[str, int] = {}
    for oracle in ORACLES:
        try:
            count = _strict_int(value[oracle], f"valid_counts.{oracle}")
        except TypeError as exc:
            problems.append(str(exc))
            continue
        if not 0 <= count <= SHARD_SIZE:
            problems.append(f"valid_counts.{oracle} is outside 0..{SHARD_SIZE}")
        counts[oracle] = count
    return counts


REPRO_FIELDS = ("word", "category", "tool", "spec", "actual")
_HEADER_RE = re.compile(r"^-\s*(word|category|tool|spec|actual)\s*:\s*(.*)$", re.IGNORECASE)


@dataclass
class Reproducer:
    path: Path
    word: str
    category: str
    tool: str
    spec: str
    actual: str
    body: str
    problems: list[str] = field(default_factory=list)

    @property
    def word_int(self) -> int | None:
        try:
            return int(self.word, 16) if self.word else None
        except ValueError:
            return None

    @property
    def shard_id(self) -> int | None:
        w = self.word_int
        return None if w is None else w >> 24


def parse_reproducer(path: Path) -> Reproducer:
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Reproducer(path, "", "", "", "", "", "", [f"unreadable: {exc}"])
    fields: dict[str, str] = {}
    body_lines: list[str] = []
    in_header = True
    for line in text.splitlines():
        match = _HEADER_RE.match(line.strip()) if in_header else None
        if match:
            fields[match.group(1).lower()] = match.group(2).strip()
            continue
        if in_header and line.strip() == "" and not fields:
            continue
        if in_header and fields and line.strip() == "":
            in_header = False
            continue
        if in_header and not _HEADER_RE.match(line.strip()):
            in_header = False
        body_lines.append(line)
    for key in REPRO_FIELDS:
        if not fields.get(key):
            problems.append(f"missing header field '{key}'")
    if fields.get("spec") and fields.get("spec") == fields.get("actual"):
        problems.append("spec and actual are identical - that is not a disagreement")
    cat = fields.get("category", "")
    if cat and cat not in TAXONOMY:
        problems.append(f"category '{cat}' is not in the taxonomy")
    tool = fields.get("tool", "")
    if tool and tool not in TOOLS:
        problems.append(f"tool '{tool}' is not one of {', '.join(TOOLS)}")
    return Reproducer(
        path=path,
        word=fields.get("word", ""),
        category=cat,
        tool=tool,
        spec=fields.get("spec", ""),
        actual=fields.get("actual", ""),
        body="\n".join(body_lines).strip(),
        problems=problems,
    )


def load_reproducers(directory: Path) -> list[Reproducer]:
    if not directory.is_dir():
        return []
    return [parse_reproducer(p) for p in sorted(directory.glob("*.md"))]


def load_flat_json(path: Path) -> Loaded:
    return _read_json(path)


def load_result_hash(path: Path) -> Loaded:
    if not path.is_file():
        return Loaded(None, f"not found: {path}", path)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return Loaded(None, f"unreadable: {exc}", path)
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        return Loaded(text or None, "not a 64-char sha256 hex digest", path)
    return Loaded(text, "", path)
