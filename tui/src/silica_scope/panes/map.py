from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.widgets import Static, TabbedContent

from .. import views
from ..widgets.spacemap import MODES, ShardChosen, ShardHighlighted, SpaceMap
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



# a mixin, not a widget: the map pane's state is the app's (which shard is
# selected drives the corpus pane too), and splitting by responsibility keeps
# each file readable without inventing a second source of truth.
@collect_handlers
class MapMixin(_Base):
    session: Session
    _narrow: bool

    if TYPE_CHECKING:  # supplied by ScopeApp or a sibling mixin; declared,
        # never defined, so this does not shadow the real one via the MRO.
        def _active_tab(self) -> str | None: ...

        def set_shard(self, shard_id: int, initial: bool = False) -> None: ...


    @on(ShardHighlighted)
    def _map_moved(self, event: ShardHighlighted) -> None:
        self._refresh_map_detail(event.shard_id)
        self._refresh_map_legend()

    @on(ShardChosen)
    def _map_chosen(self, event: ShardChosen) -> None:
        self.set_shard(event.shard_id)
        self.query_one(TabbedContent).active = "corpus"

    def _refresh_map_detail(self, shard_id: int) -> None:
        try:
            target = self.query_one("#map-detail", Static)
        except Exception:  # noqa: BLE001 - not mounted yet
            return
        shard = self.session.shard(shard_id)
        detail = views.shard_detail(shard, shard_id, self.session, compact=self._narrow)
        smap = self.query_one(SpaceMap)
        channel = Text()
        channel.append("colour: ", style="#6b7683")
        channel.append(smap.mode.label, style="#7fd1b9")
        channel.append(f"  →  {smap.describe(shard_id)}", style="#c5ced6")
        target.update(Group(detail, Text(""), channel))

    def _refresh_map_legend(self) -> None:
        try:
            legend = self.query_one("#map-legend", Static)
        except Exception:  # noqa: BLE001
            return
        smap = self.query_one(SpaceMap)
        line = Text()
        line.append("m", style="bold #7fd1b9")
        line.append(" channel  ", style="#6b7683")
        line.append(smap.mode.label, style="#c5ced6")
        legend.update(Group(Text(""), line, Text(smap.mode.caption, style="#6b7683"), smap.legend()))
        self._refresh_map_hot()

    def _refresh_map_hot(self) -> None:
        try:
            target = self.query_one("#map-hot", Static)
        except Exception:  # noqa: BLE001
            return
        smap = self.query_one(SpaceMap)
        ranked = smap.ranked(9)
        if not ranked:
            target.update(
                Group(
                    Text(""),
                    Text("nothing to rank on this channel", style="#6b7683"),
                )
            )
            return
        table = Table.grid(padding=(0, 2))
        table.add_column(width=5, no_wrap=True)
        table.add_column(width=21, justify="right", no_wrap=True)
        table.add_column(no_wrap=True)
        for shard_id, _value in ranked:
            style = "bold #7fd1b9" if shard_id == smap.cursor else "#c5ced6"
            table.add_row(
                Text(f"{shard_id:03d}", style=style),
                Text(smap.describe(shard_id), style="#8fa3b0"),
                Text(f"0x{shard_id << 24:08x}", style="#6b7683"),
            )
        target.update(
            Group(Text(""), Text(f"hottest shards - {smap.mode.label}", style="#6b7683"), table)
        )

    def key_m(self) -> None:
        if self._active_tab() != "map":
            return
        self.query_one(SpaceMap).cycle_mode(1)
        self._refresh_map_legend()

    def key_x(self) -> None:
        if self._active_tab() != "map":
            return
        self._compute_exact()

    @work(thread=True, exclusive=True, group="exact")
    def _compute_exact(self) -> None:
        from ..bitmaps import exact_disagreement_fractions

        root = self.session.artifacts.path("bitmaps")
        smap = self.query_one(SpaceMap)
        if not root.is_dir():
            self.call_from_thread(
                self.notify, f"no bitmaps/ under {self.session.root}", severity="warning"
            )
            return
        self.call_from_thread(self.notify, "reading 4 x 512 MiB of bitmaps…")
        try:
            fractions = exact_disagreement_fractions(root)
        except OSError as exc:
            self.call_from_thread(self.notify, f"bitmaps unreadable: {exc}", severity="error")
            return
        if not fractions:
            self.call_from_thread(self.notify, "bitmaps incomplete", severity="warning")
            return
        smap.exact = fractions
        self.call_from_thread(smap.refresh)
        self.call_from_thread(self._select_exact_mode)

    def _select_exact_mode(self) -> None:
        smap = self.query_one(SpaceMap)
        smap.mode_index = [m.key for m in MODES].index("exact")
        self._refresh_map_legend()
        self._refresh_map_detail(smap.cursor)
        self.notify("exact channel ready: popcount(tool XOR spec) per shard")
