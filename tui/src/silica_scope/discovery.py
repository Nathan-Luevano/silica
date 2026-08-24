from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# every path silica-scope knows how to read, relative to an artifacts root.
# `required` here means "without this the artifact root is not recognisable",
# not "without this the TUI refuses to start" - a published clean checkout
# ships only reproducers/ and result_hash.txt, and that has to work.
KNOWN_FILES: dict[str, str] = {
    "metrics": "report/metrics.json",
    "g1_metrics": "g1_metrics.json",
    "g4_metrics": "g4_metrics.json",
    "result_hash": "result_hash.txt",
    "normalization": "normalization_rule_counts.json",
    "spec_aliases": "spec_aliases.json",
}

KNOWN_DIRS: dict[str, str] = {
    "reproducers": "reproducers",
    "shards": "sweep/shards",
    "disagreements": "disagreements",
    "bitmaps": "bitmaps",
}

# marks a directory as "this is a silica artifacts dir" when walking upward.
ROOT_MARKERS = ("report/metrics.json", "result_hash.txt", "reproducers", "g4_metrics.json")


@dataclass(frozen=True)
class Presence:
    key: str
    path: Path
    present: bool
    kind: str
    detail: str = ""


@dataclass
class Artifacts:
    root: Path
    presence: dict[str, Presence] = field(default_factory=dict)

    def has(self, key: str) -> bool:
        p = self.presence.get(key)
        return bool(p and p.present)

    def path(self, key: str) -> Path:
        rel = KNOWN_FILES.get(key) or KNOWN_DIRS[key]
        return self.root / rel

    @property
    def missing(self) -> list[Presence]:
        return [p for p in self.presence.values() if not p.present]

    @property
    def found(self) -> list[Presence]:
        return [p for p in self.presence.values() if p.present]


def _looks_like_root(candidate: Path) -> bool:
    return any((candidate / marker).exists() for marker in ROOT_MARKERS)


def _search_upward(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        candidate = parent / "artifacts"
        if candidate.is_dir() and _looks_like_root(candidate):
            return candidate
        if _looks_like_root(parent) and parent.name == "artifacts":
            return parent
    return None


def locate(explicit: str | os.PathLike[str] | None = None, cwd: Path | None = None) -> Path:
    cwd = (cwd or Path.cwd()).resolve()
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("SILICA_ARTIFACTS")
    if env:
        return Path(env).expanduser().resolve()
    found = _search_upward(cwd)
    return found if found is not None else (cwd / "artifacts").resolve()


def _describe_dir(path: Path, pattern: str) -> str:
    try:
        n = sum(1 for _ in path.glob(pattern))
    except OSError:
        return "unreadable"
    return f"{n} file{'s' if n != 1 else ''}"


def _human_bytes(n: int) -> str:
    step = 1024.0
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < step or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= step
    return f"{size:.1f} TiB"


DIR_PATTERNS = {
    "reproducers": "*.md",
    "shards": "*.json",
    "disagreements": "*.zst",
    "bitmaps": "*.bin",
}


def scan(root: Path) -> Artifacts:
    presence: dict[str, Presence] = {}
    for key, rel in KNOWN_FILES.items():
        p = root / rel
        ok = p.is_file()
        detail = ""
        if ok:
            try:
                detail = _human_bytes(p.stat().st_size)
            except OSError:
                ok, detail = False, "unreadable"
        presence[key] = Presence(key, p, ok, "file", detail)
    for key, rel in KNOWN_DIRS.items():
        p = root / rel
        ok = p.is_dir()
        presence[key] = Presence(
            key, p, ok, "dir", _describe_dir(p, DIR_PATTERNS[key]) if ok else ""
        )
    return Artifacts(root=root, presence=presence)
