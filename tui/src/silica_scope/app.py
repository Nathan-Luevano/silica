from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListView,
    Static,
    TabbedContent,
    TabPane,
)

from . import views
from .corpus import CorpusUnavailable, Record, StreamStatus
from .panes.corpus import CorpusMixin
from .panes.lists import ListsMixin
from .panes.map import MapMixin
from .screens import HelpScreen, LookupScreen
from .session import Session, load
from .widgets.spacemap import SpaceMap


class Card(Vertical):
    def __init__(self, title: str, renderable: RenderableType, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._renderable = renderable
        self.add_class("card")

    def compose(self) -> ComposeResult:
        if self._title:
            yield Label(self._title, classes="card-title")
        yield Static(self._renderable)


class ScopeApp(ListsMixin, CorpusMixin, MapMixin, App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "SILICA"
    SUB_TITLE = "scope"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "quit"),
        Binding("question_mark", "help", "help", key_display="?"),
        Binding("slash,w", "lookup", "word", key_display="/"),
        Binding("r", "reload", "reload"),
        Binding("1", "tab('overview')", "overview", show=False),
        Binding("2", "tab('map')", "map", show=False),
        Binding("3", "tab('corpus')", "corpus", show=False),
        Binding("4", "tab('repro')", "reproducers", show=False),
        Binding("5", "tab('goals')", "goals", show=False),
    ]

    def __init__(self, root: Path, goals_file: Path | None = None) -> None:
        super().__init__()
        self._root = root
        self._goals_file = goals_file
        self.session: Session = load(root, goals_file)
        self.shard_id = self._default_shard()
        self.filter_index = 0
        self._narrow = False
        self.records: list[Record] = []
        self._scan_token = 0
        self._loading = False
        self._stream: Iterator[Record] | None = None
        self._stream_status = StreamStatus()
        self._exhausted = False

    # ---------- data helpers ----------

    def _default_shard(self) -> int:
        # open on a shard that has something to show: a reproducer's shard if
        # its corpus file is actually there, otherwise the first shard that
        # recorded any disagreement at all.
        corpus = self.session.corpus
        ids = corpus.shard_ids() if corpus else []
        for repro in self.session.reproducers:
            sid = repro.shard_id
            if sid is not None and sid in ids:
                return sid
        return ids[0] if ids else 0

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        if not self.session.has_anything:
            yield from self._compose_empty()
            yield Footer()
            return
        with TabbedContent(id="tabs"):
            with TabPane("overview", id="overview"):
                yield VerticalScroll(id="overview-body", classes="pane-scroll")
            with TabPane("map", id="map"), Horizontal(id="map-body"):
                # scrollable: on a short terminal the 16-row grid does not
                # fit, and clipping rows c-f with no indicator makes a quarter
                # of the shard space silently unreachable.
                with VerticalScroll(id="map-left", classes="pane-scroll"):
                    yield SpaceMap(self.session.shards, id="spacemap")
                    yield Static(id="map-legend")
                    yield Static(id="map-hot")
                yield VerticalScroll(Static(id="map-detail"), id="map-side")
            with TabPane("corpus", id="corpus"), Vertical():
                yield Static(id="corpus-controls")
                with Horizontal(id="corpus-split"):
                    yield DataTable(id="corpus-table", cursor_type="row", zebra_stripes=False)
                    yield VerticalScroll(Static(id="corpus-detail-body"), id="corpus-detail")
                yield Static(id="corpus-status")
            with TabPane("reproducers", id="repro"), Horizontal(id="repro-split"):
                yield ListView(id="repro-list")
                yield VerticalScroll(Static(id="repro-detail-body"), id="repro-detail")
            with TabPane("goals", id="goals"), Horizontal(id="goals-split"):
                yield DataTable(id="goals-table", cursor_type="row")
                yield VerticalScroll(Static(id="goals-detail-body"), id="goals-detail")
        yield Footer()

    def _compose_empty(self) -> ComposeResult:
        root = self.session.root
        names = []
        for presence in self.session.artifacts.presence.values():
            if presence.key == "goals":
                continue
            try:
                names.append(str(presence.path.relative_to(root)))
            except ValueError:
                names.append(presence.path.name)
        body = Group(
            Text("no SILICA artifacts here", style="bold #e0a03a"),
            Text(""),
            Text("searched under", style="#6b7683"),
            Text(str(root), style="#c5ced6"),
            Text(""),
            Text("for any of", style="#6b7683"),
            Text("  " + "   ".join(names), style="#6b7683"),
            Text(""),
            Text("point it somewhere real:", style="#c5ced6"),
            Text("  silica-scope /path/to/silica/artifacts", style="#7fd1b9"),
            Text("  SILICA_ARTIFACTS=/path/to/artifacts silica-scope", style="#7fd1b9"),
            Text(""),
            Text(
                "a published SILICA checkout ships reproducers/ and result_hash.txt;",
                style="#6b7683",
            ),
            Text(
                "the bitmaps, shard records and disagreement corpus are regenerated locally.",
                style="#6b7683",
            ),
        )
        with Center(id="empty-state"):
            yield Static(
                Panel(body, border_style="#e0a03a", padding=(1, 3)), id="empty-inner"
            )

    FOCUS_TARGET: ClassVar[dict[str, str]] = {
        "map": "#spacemap",
        "corpus": "#corpus-table",
        "repro": "#repro-list",
        "goals": "#goals-table",
    }

    @on(TabbedContent.TabActivated)
    def _tab_activated(self, event: TabbedContent.TabActivated) -> None:
        # without this the map's arrow keys do nothing until you happen to
        # click it, and the lists show no highlight row at all.
        selector = self.FOCUS_TARGET.get(event.pane.id or "")
        if selector is None:
            return
        with suppress(Exception):  # pane may not be built yet
            self.query_one(selector).focus()

    NARROW_AT = 110
    TINY_AT = 76

    def on_resize(self, event: events.Resize) -> None:
        self._apply_width_class(event.size.width)

    def _apply_width_class(self, width: int) -> None:
        # below ~110 columns the side-by-side splits leave the detail pane
        # too narrow to render a 32-bit word without wrapping, so the panes
        # stack instead.
        narrow = width < self.NARROW_AT
        self._narrow = narrow
        self.screen.set_class(narrow, "narrow")
        self.screen.set_class(width < self.TINY_AT, "tiny")
        with suppress(Exception):
            self.query_one(SpaceMap).compact = narrow

    def on_mount(self) -> None:
        self._apply_width_class(self.size.width)
        if not self.session.has_anything:
            return
        self._build_overview()
        self._build_repro_list()
        self._build_goals()
        self._init_corpus_table()
        self._refresh_map_detail(self.query_one(SpaceMap).cursor)
        self._refresh_map_legend()
        self.set_shard(self.shard_id, initial=True)

    # ---------- overview ----------

    def _build_overview(self) -> None:
        body = self.query_one("#overview-body", VerticalScroll)
        body.remove_children()
        session = self.session
        problems = views.problems_panel(session)
        widgets: list[Any] = []
        if problems is not None:
            widgets.append(Static(problems, classes="card"))
        widgets.append(Static(views.headline(session)))
        widgets.append(Card("per-tool agreement with the spec oracle", views.tool_table(session)))
        widgets.append(Card("disagreement taxonomy", views.category_table(session)))
        widgets.append(Card("provenance", views.provenance(session)))
        widgets.append(
            Card(
                "goals",
                Group(views.goals_table(session), views.goals_note()),
            )
        )
        widgets.append(Card("artifacts on disk", self._presence_table()))
        body.mount(*widgets)

    def _presence_table(self) -> RenderableType:
        table = Table.grid(padding=(0, 2))
        table.add_column(width=16, no_wrap=True)
        table.add_column(width=10, no_wrap=True)
        table.add_column(style="#6b7683", ratio=1)
        for presence in self.session.artifacts.presence.values():
            mark = Text("present", style="#5fbf6a") if presence.present else Text("absent", style="#6b7683")
            table.add_row(
                Text(presence.key, style="#c5ced6"),
                mark,
                f"{presence.detail}  {presence.path}" if presence.detail else str(presence.path),
            )
        return table

    # ---------- global actions ----------

    def _active_tab(self) -> str | None:
        # the empty state has no TabbedContent at all, so every pane-scoped
        # key has to survive not finding one.
        try:
            return self.query_one(TabbedContent).active
        except Exception:  # noqa: BLE001
            return None

    def action_tab(self, tab: str) -> None:
        with suppress(Exception):  # the empty state has no tabs
            self.query_one(TabbedContent).active = tab

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_lookup(self) -> None:
        if self._active_tab() is None:
            self.notify("no artifacts loaded - nothing to look a word up in", severity="warning")
            return
        self.push_screen(LookupScreen(), self._lookup_done)

    def _lookup_done(self, word: int | None) -> None:
        if word is None:
            return
        if self._active_tab() is None:
            self.notify("no artifacts loaded - nothing to look a word up in", severity="warning")
            return
        self.query_one(TabbedContent).active = "corpus"
        self._lookup_word(word)

    @work(thread=True, exclusive=True, group="lookup")
    def _lookup_word(self, word: int) -> None:
        corpus = self.session.corpus
        self.call_from_thread(
            self._corpus_detail,
            views.word_view(word, None, "looking it up in the corpus…", compact=self._narrow),
        )
        record = None
        note = "no disagreements/ on disk - cannot say whether this word disagrees"
        if corpus is not None and corpus.available():
            if corpus.has_shard(word >> 24):
                try:
                    record = corpus.lookup(word)
                except CorpusUnavailable as exc:
                    note = str(exc)
                else:
                    note = (
                        # absence proves validity agreement, which is
                        # exhaustive. it proves nothing about text, which
                        # this corpus only sampled (0.079% of the population)
                        # - saying "all four agreed" flat would present a
                        # sample as exhaustive.
                        "not in the corpus.\n"
                        "all four agreed on validity (exhaustive).\n"
                        "text was not sampled for this word."
                        if record is None
                        else ""
                    )
            else:
                note = (
                    f"shard {word >> 24:03d} recorded no disagreements at all - "
                    "all four oracles agreed on validity across its whole range"
                )
        self.call_from_thread(
            self._corpus_detail, views.word_view(word, record, note, compact=self._narrow)
        )
        banner = Text()
        banner.append("showing ", style="#6b7683")
        banner.append(f"0x{word:08x}", style="#7fd1b9")
        banner.append(
            " from the word lookup - move in the table to go back to browsing",
            style="#6b7683",
        )
        self.call_from_thread(self._corpus_status, banner)

    def _corpus_detail(self, renderable: RenderableType) -> None:
        self.query_one("#corpus-detail-body", Static).update(renderable)

    def action_reload(self) -> None:
        had_panes = self._active_tab() is not None
        self.session = load(self._root, self._goals_file)
        if not self.session.has_anything or not had_panes:
            # the empty state has no panes to rebuild, and a tree that only
            # just grew artifacts needs a restart to build them.
            self.notify(
                f"reloaded from {self.session.root}"
                + ("" if self.session.has_anything else " - still no artifacts there"),
                severity="information" if self.session.has_anything else "warning",
            )
            return
        # every pane, not just the overview: a stale map or goals tab that
        # contradicts the freshly reloaded overview is worse than no reload.
        self._build_overview()
        self._rebuild_map()
        self._build_repro_list()
        self._rebuild_goals()
        self.set_shard(self.shard_id)
        self.notify(f"reloaded from {self.session.root}")


