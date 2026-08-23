from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual import on
from textual.widgets import DataTable, ListItem, ListView, Static, TabbedContent

from .. import views
from ..fmt import truncate
from ..model import GOAL_ARTIFACTS
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
        for n, repro in enumerate(self.session.reproducers, start=1):
            label = Text(no_wrap=True, overflow="ellipsis")
            label.append(f"{n:2d} ", style="#3d4650")
            label.append(repro.word or "??", style="#7fd1b9")
            label.append(" ")
            label.append(
                truncate(views.SHORT_CATEGORY.get(repro.category, repro.category), 14),
                style=views.CATEGORY_STYLE.get(repro.category, "white"),
            )
            label.append(f" {repro.tool}", style="#6b7683")
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
                    style="#6b7683",
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


    def _rebuild_goals(self) -> None:
        table = self.query_one("#goals-table", DataTable)
        table.clear(columns=True)
        self._build_goals()

    def _build_goals(self) -> None:
        table = self.query_one("#goals-table", DataTable)
        table.add_column("goal", width=6)
        table.add_column("status", width=12)
        if not self.session.goals:
            self.query_one("#goals-detail-body", Static).update(views.goals_table(self.session))
            return
        for goal in self.session.goals:
            table.add_row(Text(goal.id, style="bold"), views.status_text(goal))
        self._show_goal(0)

    @on(DataTable.RowHighlighted, "#goals-table")
    def _goal_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_goal(event.cursor_row)

    def _show_goal(self, index: int) -> None:
        if not (0 <= index < len(self.session.goals)):
            return
        goal = self.session.goals[index]
        rows: list[RenderableType] = [
            Text(goal.id, style="bold #7fd1b9"),
            Text(""),
            Text(goal.statement, style="#c5ced6"),
            Text(""),
            Text(f"verifier      {goal.verifier}", style="#6b7683"),
            Text(f"source        {goal.verifier_file}", style="#6b7683"),
        ]
        if goal.verifier_sha256:
            rows.append(Text(f"pinned sha    {goal.verifier_sha256}", style="#6b7683"))
        rows.extend([Text(""), views.status_text(goal), Text("")])
        keys = GOAL_ARTIFACTS.get(goal.id, ())
        if keys:
            table = Table.grid(padding=(0, 2))
            table.add_column(width=16, no_wrap=True)
            table.add_column(width=9, no_wrap=True)
            table.add_column(style="#6b7683")
            for key in keys:
                presence = self.session.artifacts.presence.get(key)
                if presence is None:
                    continue
                mark = (
                    Text("present", style="#5fbf6a")
                    if presence.present
                    else Text("absent", style="#6b7683")
                )
                rel = presence.path
                try:
                    shown = str(rel.relative_to(self.session.root))
                except ValueError:
                    shown = str(rel)
                table.add_row(Text(key, style="#c5ced6"), mark, shown)
            rows.extend(
                [Text("artifacts this goal produces", style="#6b7683"), table, Text("")]
            )
        rows.append(views.goals_note())
        self.query_one("#goals-detail-body", Static).update(Group(*rows))
