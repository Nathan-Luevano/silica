from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.containers import VerticalScroll
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
        self._keep_cursor_visible(event.shard_id)

    def _keep_cursor_visible(self, shard_id: int) -> None:
        try:
            container = self.query_one("#map-left", VerticalScroll)
        except Exception:  # noqa: BLE001 - not mounted yet
            return
        row = 1 + shard_id // 16  # +1 for the column header line
        top = container.scroll_offset.y
        height = max(container.size.height, 1)
        if row <= top:
            # keep the column-header line in view when scrolling up
            container.scroll_to(y=max(row - 1, 0), animate=False)
        elif row >= top + height:
            container.scroll_to(y=row - height + 1, animate=False)

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
        channel.append("colour: ", style=views.DIM)
        channel.append(smap.mode.label, style=views.ACCENT_COLOR)
        channel.append(f"  ->  {smap.describe(shard_id)}", style=views.FG)
        target.update(Group(detail, Text(""), channel))

    def _refresh_map_legend(self) -> None:
        try:
            legend = self.query_one("#map-legend", Static)
        except Exception:  # noqa: BLE001
            return
        smap = self.query_one(SpaceMap)
        line = Text()
        line.append("m", style=views.ACCENT)
        line.append(" channel  ", style=views.DIM)
        line.append(smap.mode.label, style=views.FG)
        legend.update(Group(Text(""), line, Text(smap.mode.caption, style=views.DIM), smap.legend()))
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
                    Text("nothing to rank on this channel", style=views.DIM),
                )
            )
            return
        table = Table.grid(padding=(0, 2))
        table.add_column(width=5, no_wrap=True)
        table.add_column(width=21, justify="right", no_wrap=True)
        table.add_column(no_wrap=True)
        for shard_id, _value in ranked:
            style = views.ACCENT if shard_id == smap.cursor else views.FG
            table.add_row(
                Text(f"{shard_id:03d}", style=style),
                Text(smap.describe(shard_id), style=views.FG),
                Text(f"0x{shard_id << 24:08x}", style=views.DIM),
            )
        target.update(
            Group(Text(""), Text(f"hottest shards - {smap.mode.label}", style=views.DIM), table)
        )

    def key_m(self) -> None:
        if self._active_tab() != "map":
            return
        self.query_one(SpaceMap).cycle_mode(1)
        self._refresh_map_legend()
        self._announce_mode()

    def key_upper_m(self) -> None:  # textual names shift+m "upper_m"
        if self._active_tab() != "map":
            return
        self.query_one(SpaceMap).cycle_mode(-1)
        self._refresh_map_legend()
        self._announce_mode()

    def _announce_mode(self) -> None:
        # the grid itself never moves - m only recolours it by a different
        # metric, and on a near-monochrome palette that recolouring can be
        # too subtle to notice at a glance. say what changed out loud.
        mode = self.query_one(SpaceMap).mode
        self.notify(f"channel: {mode.label} - {mode.caption}")

    def _rebuild_map(self) -> None:
        smap = self.query_one(SpaceMap)
        smap.set_shards(self.session.shards)
        self._refresh_map_detail(smap.cursor)
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
        self.call_from_thread(self.notify, "reading 4 x 512 MiB of bitmaps...")
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
