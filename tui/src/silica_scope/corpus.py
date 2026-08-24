from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import ORACLES, SHARD_SIZE, TAXONOMY

CHUNK = 1 << 20
# zstandard releases the GIL while decompressing, and a real terminal was
# measured answering a keypress in 30 ms while indexing the densest shard, so
# no artificial yielding is needed here. (A ~3 s stall shows up under
# textual's run_test pilot, but that is the harness waiting for the worker's
# message queue to settle, not input latency.) What did matter was throttling
# the progress callback - see panes/corpus.py.
SUPPORTED_FORMAT_VERSIONS = frozenset({1})

# the sweep records unicorn's verdict as this literal when the oracle said
# "valid" but produced no disassembly text. comparing it to the spec's real
# mnemonic would report a text disagreement that nobody measured.
PLACEHOLDER_TEXT = frozenset({"<valid>", "<invalid>"})


def is_placeholder(text: str | None) -> bool:
    return text is None or text.strip() in PLACEHOLDER_TEXT


def _key_markers(key: str, value: str) -> tuple[bytes, ...]:
    # the real corpus is written compact ("category":"X") while
    # docs/formats.md's example is pretty-printed ("category": "X").
    # match either rather than betting on one.
    return (f'"{key}":"{value}"'.encode(), f'"{key}": "{value}"'.encode())


class CorpusUnavailable(Exception):
    pass


def _decompressor() -> Any:
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - hard dependency
        raise CorpusUnavailable(
            "the 'zstandard' package is required to read disagreements/*.zst"
        ) from exc
    return zstandard.ZstdDecompressor()


@dataclass
class Record:
    word: int
    category: str
    oracle_valid: dict[str, bool]
    oracle_text: dict[str, str | None]
    format_version: int = 1

    @property
    def hex(self) -> str:
        return f"0x{self.word:08x}"

    @property
    def shard_id(self) -> int:
        return self.word >> 24

    def validity_disagreements(self) -> list[str]:
        # validity only, and deliberately so: it is decidable straight from
        # the bitmaps. text is not - the spec oracle emits a bare mnemonic,
        # so comparing strings flags every tool on every text-tier record.
        # the record's own `category` is the sweep's normalized verdict.
        spec = self.oracle_valid.get("spec")
        return [
            name for name in ORACLES if name != "spec" and self.oracle_valid.get(name) != spec
        ]


def parse_record(line: bytes) -> Record | None:
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict):
        return None
    version = d.get("format_version")
    # docs/formats.md: consumers must reject unknown versions, not guess.
    if version not in SUPPORTED_FORMAT_VERSIONS:
        return None
    raw_word = d.get("word")
    try:
        word = int(str(raw_word), 16)
    except (TypeError, ValueError):
        return None
    valid = d.get("oracle_valid") or {}
    text = d.get("oracle_text") or {}
    return Record(
        word=word,
        category=str(d.get("category", "?")),
        oracle_valid={k: bool(v) for k, v in valid.items()} if isinstance(valid, dict) else {},
        oracle_text=(
            {k: (None if v is None else str(v)) for k, v in text.items()}
            if isinstance(text, dict)
            else {}
        ),
        format_version=int(version),
    )


@dataclass
class StreamStatus:
    # a truncated zstd frame does not raise: the reader just returns EOF
    # early. for newline-delimited JSON the tell is that the stream stopped
    # mid-record, so the last thing read is not a newline.
    truncated: bool = False


@dataclass
class ShardIndex:
    shard_id: int
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    bad_lines: int = 0
    truncated: bool = False

    @property
    def text_tier_total(self) -> int:
        return sum(v for k, v in self.counts.items() if k != "VALIDITY")

    @property
    def classified(self) -> int:
        return sum(self.counts.values())


class Corpus:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._index: dict[int, ShardIndex] = {}

    def available(self) -> bool:
        return self.directory.is_dir()

    def shard_path(self, shard_id: int) -> Path:
        return self.directory / f"{shard_id:03d}.zst"

    def has_shard(self, shard_id: int) -> bool:
        # Path.is_file() propagates EACCES rather than swallowing it, and an
        # unreadable disagreements/ must not take the whole reader down.
        try:
            return self.shard_path(shard_id).is_file()
        except OSError:
            return False

    def shard_ids(self) -> list[int]:
        if not self.available():
            return []
        out = []
        try:
            candidates = sorted(self.directory.glob("*.zst"))
        except OSError:
            return []
        for p in candidates:
            try:
                out.append(int(p.stem, 10))
            except ValueError:
                continue
        return out

    def _stream(
        self,
        shard_id: int,
        markers: list[bytes] | None = None,
        on_progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        status: StreamStatus | None = None,
    ) -> Iterator[bytes]:
        path = self.shard_path(shard_id)
        if not self.has_shard(shard_id):
            raise CorpusUnavailable(f"no corpus file for shard {shard_id:03d} ({path})")
        try:
            size = path.stat().st_size or 1
        except OSError as exc:
            raise CorpusUnavailable(f"cannot read {path}: {exc}") from exc
        dctx = _decompressor()
        with path.open("rb") as fh:
            reader = dctx.stream_reader(fh)
            tail = b""
            while True:
                if cancelled is not None and cancelled():
                    return
                try:
                    chunk = reader.read(CHUNK)
                except Exception as exc:
                    raise CorpusUnavailable(f"corrupt zstd stream in {path.name}: {exc}") from exc
                if not chunk:
                    break
                if on_progress is not None:
                    on_progress(min(fh.tell() / size, 1.0))
                buf = tail + chunk
                cut = buf.rfind(b"\n")
                if cut < 0:
                    tail = buf
                    continue
                tail = buf[cut + 1 :]
                body = buf[:cut]
                # the whole point of the chunk-level check: a filtered browse
                # of a dense shard is 11M lines of VALIDITY, and splitting
                # every one of them to throw it away is what makes a naive
                # filter take ten seconds instead of one.
                if markers and not any(m in body for m in markers):
                    continue
                yield from body.split(b"\n")
            if tail.strip():
                # a complete ndjson stream ends on a newline; leftovers mean
                # the frame stopped mid-record.
                if status is not None:
                    status.truncated = True
                if not markers or any(m in tail for m in markers):
                    yield tail
        if on_progress is not None:
            on_progress(1.0)

    def iter_records(
        self,
        shard_id: int,
        categories: frozenset[str] | None = None,
        limit: int | None = None,
        on_progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        status: StreamStatus | None = None,
    ) -> Iterator[Record]:
        # the raw-bytes prefilter matters: a dense shard is ~11M lines, and
        # json.loads on every one of them to throw 99.99% away is the
        # difference between a snappy filter and a 30-second hang.
        markers: list[bytes] = []
        if categories:
            for c in sorted(categories):
                markers.extend(_key_markers("category", c))
        emitted = 0
        for line in self._stream(shard_id, markers or None, on_progress, cancelled, status):
            if not line:
                continue
            if markers and not any(m in line for m in markers):
                continue
            record = parse_record(line)
            if record is None:
                continue
            yield record
            emitted += 1
            if limit is not None and emitted >= limit:
                return

    def index_shard(
        self,
        shard_id: int,
        progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> ShardIndex:
        cached = self._index.get(shard_id)
        if cached is not None:
            return cached
        idx = ShardIndex(shard_id)
        path = self.shard_path(shard_id)
        if not path.is_file():
            self._index[shard_id] = idx
            return idx
        # both spellings, always. probing one line and locking the shard to
        # that variant was wrong: a real shard mixes them - 008.zst holds
        # 6,549,504 compact VALIDITY records and 1,766 spaced text-tier ones,
        # and the probe threw away the interesting 1,766.
        markers = {c: _key_markers("category", c) for c in TAXONOMY}
        overlap = max(len(m) for variants in markers.values() for m in variants) - 1
        dctx = _decompressor()
        counts = dict.fromkeys(TAXONOMY, 0)
        total = 0
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        last_chunk = b""
        with path.open("rb") as fh:
            reader = dctx.stream_reader(fh)
            carry = b""
            while True:
                try:
                    chunk = reader.read(CHUNK)
                except Exception as exc:
                    raise CorpusUnavailable(f"corrupt zstd stream in {path.name}: {exc}") from exc
                if not chunk:
                    break
                if cancelled is not None and cancelled():
                    raise CorpusUnavailable("indexing cancelled")
                last_chunk = chunk
                total += chunk.count(b"\n")
                head = chunk[:overlap]
                bridge = carry + head
                for cat, variants in markers.items():
                    for marker in variants:
                        # matches wholly inside chunk, plus the ones that
                        # straddle the previous chunk boundary (counted once:
                        # the bridge's own interior hits are subtracted back
                        # out, they belong to the neighbouring chunks).
                        counts[cat] += (
                            chunk.count(marker)
                            + bridge.count(marker)
                            - carry.count(marker)
                            - head.count(marker)
                        )
                carry = chunk[-overlap:] if overlap else b""
                if progress is not None and size:
                    progress(min(fh.tell() / size, 1.0))
        idx.counts = {k: v for k, v in counts.items() if v}
        idx.total = total
        idx.truncated = bool(last_chunk) and not last_chunk.endswith(b"\n")
        if idx.truncated:
            total += 1  # the partial final record still exists on disk
            idx.total = total
        idx.bad_lines = max(total - idx.classified, 0)
        self._index[shard_id] = idx
        if progress is not None:
            progress(1.0)
        return idx

    def cached_index(self, shard_id: int) -> ShardIndex | None:
        return self._index.get(shard_id)

    def lookup(self, word: int) -> Record | None:
        shard_id = word >> 24
        if not self.has_shard(shard_id):
            return None
        needles = _key_markers("word", f"0x{word:08x}")
        for line in self._stream(shard_id):
            if any(n in line for n in needles):
                return parse_record(line)
        return None

    def shard_bytes(self, shard_id: int) -> int:
        try:
            return self.shard_path(shard_id).stat().st_size
        except OSError:
            return 0


def word_range(shard_id: int) -> tuple[int, int]:
    return shard_id * SHARD_SIZE, (shard_id + 1) * SHARD_SIZE
