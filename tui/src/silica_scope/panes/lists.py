from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Group
from rich.text import Text
from textual import on
from textual.widgets import ListItem, ListView, Static, TabbedContent

from .. import views
from ..fmt import truncate
from . import collect_handlers

if TYPE_CHECKING:
    from textual.app import App

    from ..session import Session

    # at runtime these are plain mixins folded into ScopeApp; for the type
    # checker they are the App, which is where call_from_thread, query_one
    # and the shared session actually live.
    _Base = App[None]
else:
    _Base = object


@collect_handlers
class ListsMixin(_Base):
    session: Session
    shard_id: int

    if TYPE_CHECKING:  # supplied by CorpusMixin; declared, never defined.
        def set_shard(self, shard_id: int, initial: bool = False) -> None: ...

    # ScopeApp's real one is a @work(thread=True), whose decorated signature
    # is a Worker factory - hence Any rather than a matching stub.
    _lookup_word: Any

    def _build_repro_list(self) -> None:
        listing = self.query_one("#repro-list", ListView)
        listing.clear()
        if not self.session.reproducers:
            self.query_one("#repro-detail-body", Static).update(
                views.missing_panel(
                    "reproducers",
                    str(self.session.artifacts.path("reproducers")),
                    "G6's output is a directory of markdown writeups, one per disagreement",
                )
            )
            return
        for repro in self.session.reproducers:
            # no ordinal prefix here: it looked like a number-jump shortcut
            # but nothing bound to it, and the same "N of total" is already
            # in the detail pane below - a number here would just repeat it.
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append(repro.word or "??", style=views.ACCENT_COLOR)
            label.append(" ")
            label.append(
                truncate(views.SHORT_CATEGORY.get(repro.category, repro.category), 14),
                style=views.FG,
            )
            label.append(f" {repro.tool}", style=views.DIM)
            listing.append(ListItem(Static(label)))
        listing.index = 0
        self._show_repro(0)

    @on(ListView.Highlighted, "#repro-list")
    def _repro_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.index is not None:
            self._show_repro(event.list_view.index)

    def _show_repro(self, index: int) -> None:
        if not (0 <= index < len(self.session.reproducers)):
            return
        repro = self.session.reproducers[index]
        self.query_one("#repro-detail-body", Static).update(
            Group(
                views.reproducer_detail(repro),
                Text(""),
                Text(
                    f"{index + 1} of {len(self.session.reproducers)}"
                    "     enter  look this word up in the corpus",
                    style=views.DIM,
                ),
            )
        )

    @on(ListView.Selected, "#repro-list")
    def _repro_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None:
            return
        word = self.session.reproducers[index].word_int
        if word is None:
            self.notify("that reproducer has no parseable word", severity="warning")
            return
        self.query_one(TabbedContent).active = "corpus"
        if word >> 24 != self.shard_id:
            self.set_shard(word >> 24)
        self._lookup_word(word)
