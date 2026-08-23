from __future__ import annotations

import json
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


def _num(d: dict[str, Any], key: str, warnings: list[str], where: str, default: Any = 0) -> Any:
    v = d.get(key, None)
    if v is None:
        warnings.append(f"{where}: missing '{key}'")
        return default
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        warnings.append(f"{where}: '{key}' is not numeric ({type(v).__name__})")
        return default
    return v


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
            validity_disagreements=int(_num(blob, "validity_disagreements_with_spec", warnings, where)),
            validity_agreement_micro=float(_num(blob, "validity_agreement_micro", warnings, where)),
            macro_validity_agreement=float(_num(blob, "macro_validity_agreement", warnings, where)),
            text_disagreements=int(_num(blob, "text_tier_disagreements_with_spec", warnings, where)),
            text_agreement_micro=float(_num(blob, "text_tier_agreement_micro", warnings, where)),
            text_method=str(blob.get("text_tier_method", "unknown")),
            text_sample_size=int(_num(blob, "text_tier_sample_size", warnings, where)),
            text_population=int(_num(blob, "text_tier_population", warnings, where)),
        )
    ranking = data.get("tool_ranking_worst_first")
    if not isinstance(ranking, list) or not all(isinstance(x, str) for x in ranking):
        warnings.append("missing or malformed 'tool_ranking_worst_first'")
        ranking = sorted(per_tool, key=lambda n: per_tool[n].validity_agreement_micro)
    total = int(_num(data, "total_words", warnings, "root", TOTAL_WORDS))
    if total != TOTAL_WORDS:
        warnings.append(f"total_words is {total:,}, expected {TOTAL_WORDS:,}")
    return Loaded(
        Metrics(
            format_version=int(_num(data, "format_version", warnings, "root", 0)),
            total_words=total,
            spec_valid_count=int(_num(data, "spec_valid_count", warnings, "root")),
            per_tool=per_tool,
            ranking_worst_first=list(ranking),
            warnings=warnings,
        ),
        "",
        path,
    )


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
    for path in sorted(directory.glob("*.json")):
        loaded = _read_json(path)
        if not loaded.ok:
            problems.append(f"{path.name}: {loaded.error}")
            continue
        d = loaded.value
        if not isinstance(d, dict):
            problems.append(f"{path.name}: not an object")
            continue
        try:
            counts = d.get("valid_counts") or {}
            shards.append(
                Shard(
                    shard_id=int(d["shard_id"]),
                    start=int(d.get("start", 0)),
                    end=int(d.get("end", 0)),
                    valid_counts={k: int(v) for k, v in counts.items()},
                    crash_count=int(d.get("crash_count", 0)),
                    untriaged_crash_count=int(d.get("untriaged_crash_count", 0)),
                    content_hash=str(d.get("content_hash", "")),
                    duration_ms=int(d.get("duration_ms", 0)),
                    status=str(d.get("status", "unknown")),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"{path.name}: {exc}")
    shards.sort(key=lambda s: s.shard_id)
    return shards, problems


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


# which published artifacts each goal's own output lands in. this is a map
# for cross-referencing what is on disk, not a claim that anything here was
# verified - only `silica verify` writes status.
GOAL_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "G1": ("g1_metrics",),
    "G2": ("shards", "bitmaps"),
    "G3": ("normalization", "spec_aliases"),
    "G4": ("g4_metrics", "disagreements"),
    "G5": ("metrics",),
    "G6": ("reproducers",),
    "G7": ("result_hash",),
}


@dataclass
class Goal:
    id: str
    statement: str
    verifier: str
    verifier_file: str
    status: str
    verifier_sha256: str = ""


def load_goals(path: Path | None) -> tuple[list[Goal], str]:
    if path is None or not path.is_file():
        return [], "GOALS.yml not found"
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is a hard dependency
        return [], "PyYAML not installed"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, Exception) as exc:  # noqa: BLE001 - yaml raises many types
        return [], f"unreadable: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("goals"), list):
        return [], "expected a top-level 'goals:' list"
    goals: list[Goal] = []
    for entry in data["goals"]:
        if not isinstance(entry, dict):
            continue
        goals.append(
            Goal(
                id=str(entry.get("id", "?")),
                statement=str(entry.get("statement", "")),
                verifier=str(entry.get("verifier", "")),
                verifier_file=str(entry.get("verifier_file", "")),
                status=str(entry.get("status", "unknown")),
                verifier_sha256=str(entry.get("verifier_sha256", "")),
            )
        )
    return goals, ""


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
