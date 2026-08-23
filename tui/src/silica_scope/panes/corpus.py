from __future__ import annotations

import time
from itertools import islice
from typing import TYPE_CHECKING

from rich.console import Group, RenderableType
from rich.text import Text
from textual import on, work
from textual.widgets import DataTable, Static

from .. import views
from ..corpus import CorpusUnavailable, Record, StreamStatus, is_placeholder
from ..fmt import commas, human_bytes, truncate
from ..model import TAXONOMY
from ..screens import ShardPrompt
from . import collect_handlers

if TYPE_CHECKING:
    from collections.abc import Iterator

    from textual.app import App

    from ..session import Session

    # at runtime these are plain mixins folded into ScopeApp; for the type
    # checker they are the App, which is where call_from_thread, query_one
    # and the shared session actually live.
    _Base = App[None]
else:
    _Base = object


PAGE = 400
TEXT_TIER = frozenset(t for t in TAXONOMY if t != "VALIDITY")


@collect_handlers
class CorpusMixin(_Base):
    session: Session
    _narrow: bool
    shard_id: int
    filter_index: int
    records: list[Record]
    _scan_token: int
    _loading: bool
    _exhausted: bool
    _stream: Iterator[Record] | None
    _stream_status: StreamStatus

    if TYPE_CHECKING:  # supplied by ScopeApp; declared, never defined, so
        # the mixin does not shadow the real method through the MRO.
        def _active_tab(self) -> str | None: ...

    @property
    def filter_label(self) -> str:
        if self.filter_index == 0:
            return "all categories"
        if self.filter_index == 1:
            return "text tier only"
        return TAXONOMY[self.filter_index - 2]

    @property
    def filter_set(self) -> frozenset[str] | None:
        if self.filter_index == 0:
            return None
        if self.filter_index == 1:
            return TEXT_TIER
        return frozenset({TAXONOMY[self.filter_index - 2]})


    def _init_corpus_table(self) -> None:
        table = self.query_one("#corpus-table", DataTable)
        table.add_column("word", width=10)
        table.add_column("category", width=15)
        table.add_column("cap", width=3)
        table.add_column("llvm", width=4)
        table.add_column("spec", width=4)
        table.add_column("uni", width=3)
        table.add_column("llvm text", width=16)
        table.add_column("spec", width=10)

    def set_shard(self, shard_id: int, initial: bool = False) -> None:
        self.shard_id = max(0, min(255, shard_id))
        self.records = []
        self._scan_token += 1
        self._stream = None
        self._exhausted = False
        table = self.query_one("#corpus-table", DataTable)
        table.clear()
        self.query_one("#corpus-detail-body", Static).update(
            Text("select a row to inspect the encoding", style="#6b7683")
        )
        self._refresh_corpus_controls()
        corpus = self.session.corpus
        if corpus is None or not corpus.available():
            self._corpus_status(
                Text(
                    f"no disagreements/ under {self.session.root} - nothing to browse",
                    style="#e0a03a",
                )
            )
            return
        if not corpus.has_shard(self.shard_id):
            self._corpus_status(
                Text(
                    f"shard {self.shard_id:03d} recorded no disagreements "
                    "(the sweep writes no file at all for a clean shard)",
                    style="#6b7683",
                )
            )
            return
        self._scan(self._scan_token, reset=True)

    def _refresh_corpus_controls(self) -> None:
        corpus = self.session.corpus
        shard = self.session.shard(self.shard_id)
        line = Text()
        line.append(f" shard {self.shard_id:03d} ", style="bold #0d1117 on #7fd1b9")
        if shard is not None:
            line.append(f"  0x{shard.start:08x}..0x{shard.end - 1:08x}", style="#6b7683")
        line.append("   filter ", style="#6b7683")
        line.append(self.filter_label, style="#5aa9e6")
        if corpus is not None and corpus.has_shard(self.shard_id):
            line.append(
                f"   corpus {human_bytes(corpus.shard_bytes(self.shard_id))}", style="#6b7683"
            )
        index = corpus.cached_index(self.shard_id) if corpus else None
        rows: list[RenderableType] = [line]
        if index is not None and index.truncated:
            rows.append(
                Text(
                    " ! this shard's .zst ends mid-record - it is truncated, "
                    "so these counts are of what survives",
                    style="#e0335b",
                )
            )
        if index is not None and index.counts:
            counts = Text()
            counts.append(" indexed: ", style="#6b7683")
            counts.append(f"{commas(index.total)} records", style="#c5ced6")
            for name, count in sorted(index.counts.items(), key=lambda kv: -kv[1]):
                counts.append("   ")
                counts.append(name, style=views.CATEGORY_STYLE.get(name, "white"))
                counts.append(f" {commas(count)}", style="#c5ced6")
            if index.bad_lines:
                counts.append(f"   {commas(index.bad_lines)} unclassified", style="#e0a03a")
            rows.append(counts)
        else:
            rows.append(
                Text(
                    " s shard   f filter   n next page   i index this shard", style="#6b7683"
                )
            )
        self.query_one("#corpus-controls", Static).update(Group(*rows))

    def _corpus_status(self, renderable: RenderableType) -> None:
        self.query_one("#corpus-status", Static).update(renderable)

    def _valid_cell(self, record: Record, oracle: str) -> Text:
        value = record.oracle_valid.get(oracle)
        if value is None:
            return Text(" ? ", style="#6b7683")
        return Text(" ✓ ", style="#5fbf6a") if value else Text(" ✗ ", style="#e0335b")

    def _add_rows(self, records: list[Record]) -> None:
        table = self.query_one("#corpus-table", DataTable)
        for record in records:
            raw_llvm, raw_spec = record.oracle_text.get("llvm"), record.oracle_text.get("spec")
            llvm = "" if is_placeholder(raw_llvm) else views.clean(raw_llvm or "")
            spec = "" if is_placeholder(raw_spec) else views.clean(raw_spec or "")
            table.add_row(
                Text(record.hex, style="#7fd1b9"),
                views.category_text(record.category, short=True),
                self._valid_cell(record, "capstone"),
                self._valid_cell(record, "llvm"),
                self._valid_cell(record, "spec"),
                self._valid_cell(record, "unicorn"),
                Text(truncate(llvm, 16), style="#c5ced6") if llvm else Text("·", style="#3d4650"),
                Text(truncate(spec, 10), style="#7fd1b9") if spec else Text("·", style="#3d4650"),
            )
        self.records.extend(records)

    @work(thread=True, exclusive=True, group="scan")
    def _scan(self, token: int, reset: bool) -> None:
        corpus = self.session.corpus
        if corpus is None:
            return
        self._loading = True
        self.call_from_thread(self._corpus_status, Text("scanning…", style="#e0a03a"))
        found: list[Record] = []
        try:
            self._stream_status = StreamStatus()
            if reset or self._stream is None:
                # records inside a shard are NOT word-ordered (the text tier is
                # a reservoir sample), so paging by "next word after the last
                # one shown" silently drops rows. keep the decompressor open
                # and pull the next page off the same stream instead.
                self._stream = corpus.iter_records(
                    self.shard_id,
                    categories=self.filter_set,
                    cancelled=lambda: token != self._scan_token,
                    status=self._stream_status,
                )
            found = list(islice(self._stream, PAGE))
        except CorpusUnavailable as exc:
            self._loading = False
            self._stream = None
            self.call_from_thread(self._corpus_status, Text(str(exc), style="#e0335b"))
            return
        self._loading = False
        if token != self._scan_token:
            return
        self._exhausted = len(found) < PAGE
        self.call_from_thread(self._scan_done, found, reset)

    def _scan_done(self, found: list[Record], reset: bool) -> None:
        self._add_rows(found)
        table = self.query_one("#corpus-table", DataTable)
        if reset and found:
            table.move_cursor(row=0)
            self._show_record(0)
        status = Text()
        if not self.records:
            status.append(
                f"no {self.filter_label} records in shard {self.shard_id:03d}",
                style="#e0a03a",
            )
            status.append("   press f to widen the filter", style="#6b7683")
        else:
            status.append(f"{commas(len(self.records))} loaded", style="#c5ced6")
            if self._exhausted:
                status.append("   end of this shard's matches", style="#6b7683")
            else:
                status.append("   n for the next page", style="#6b7683")
            index = self.session.corpus.cached_index(self.shard_id) if self.session.corpus else None
            if index is None:
                status.append("   i to count this shard exactly", style="#6b7683")
            if self._stream_status.truncated:
                status.append("   ! truncated .zst", style="#e0335b")
        self._corpus_status(status)
        self._refresh_corpus_controls()

    def _show_record(self, row: int) -> None:
        if not (0 <= row < len(self.records)):
            return
        record = self.records[row]
        self.query_one("#corpus-detail-body", Static).update(
            views.word_view(record.word, record, compact=self._narrow)
        )

    @on(DataTable.RowHighlighted, "#corpus-table")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_record(event.cursor_row)

    def key_s(self) -> None:
        if self._active_tab() != "corpus":
            return
        self.push_screen(ShardPrompt(), self._shard_chosen)

    def _shard_chosen(self, shard_id: int | None) -> None:
        if shard_id is not None:
            self.set_shard(shard_id)

    def key_f(self) -> None:
        if self._active_tab() != "corpus":
            return
        self.filter_index = (self.filter_index + 1) % (2 + len(TAXONOMY))
        self.set_shard(self.shard_id)

    def key_escape(self) -> None:
        if self._active_tab() != "corpus" or not self._loading:
            return
        # bump the token: the worker checks it every chunk and stops.
        self._scan_token += 1
        self._stream = None
        self._loading = False
        self._corpus_status(Text("scan cancelled - n to start a fresh page", style="#e0a03a"))

    def key_n(self) -> None:
        if self._active_tab() != "corpus" or self._loading or self._exhausted:
            return
        if self.records:
            self._scan(self._scan_token, reset=False)

    def key_i(self) -> None:
        if self._active_tab() != "corpus":
            return
        self._index_shard()

    @work(thread=True, exclusive=True, group="index")
    def _index_shard(self) -> None:
        corpus = self.session.corpus
        if corpus is None or not corpus.has_shard(self.shard_id):
            return
        shard_id = self.shard_id

        # throttled hard: call_from_thread blocks the worker until the UI
        # thread runs the callable, so one update per chunk means thousands
        # of them queued ahead of the user's next keystroke - measured at a
        # 3-second input stall on a dense shard.
        last = [0.0, -1]

        def progress(fraction: float) -> None:
            now = time.monotonic()
            percent = int(fraction * 100)
            if percent == last[1] or now - last[0] < 0.1:
                return
            last[0], last[1] = now, percent
            self.call_from_thread(
                self._corpus_status,
                Text(f"indexing shard {shard_id:03d}: {percent}%", style="#e0a03a"),
            )

        try:
            corpus.index_shard(shard_id, progress=progress)
        except CorpusUnavailable as exc:
            self.call_from_thread(self._corpus_status, Text(str(exc), style="#e0335b"))
            return
        self.call_from_thread(self._refresh_corpus_controls)
        self.call_from_thread(
            self._corpus_status, Text(f"shard {shard_id:03d} indexed", style="#5fbf6a")
        )
