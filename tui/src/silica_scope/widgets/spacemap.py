from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding, BindingType
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

from ..fmt import abbreviate, human_bytes, pct
from ..model import Shard

# black -> deep green -> amber -> red. reads as an instrument, and the
# steps are far enough apart to survive a low-contrast terminal theme.
HEAT = (
    "#12161c",
    "#12351f",
    "#1c5a2a",
    "#3f8c2c",
    "#7fae24",
    "#c39a1a",
    "#d4741d",
    "#cf4a2b",
    "#e0335b",
)
EMPTY = "#1a1d23"
CELL = "██"
CELL_W = 3
GUTTER = 3
COLS = 16
LABEL_STYLE = Style(color="#4a545f")
HEADER_STYLE = Style(color="#6b7683")
CURSOR_STYLE = Style(color="#ffffff", bold=True)
CURSOR_DIM = Style(color="#5c6773")


@dataclass(frozen=True)
class MapMode:
    key: str
    label: str
    caption: str


MODES: tuple[MapMode, ...] = (
    MapMode("spread", "oracle spread", "widest gap between two oracles' valid counts"),
    MapMode("corpus", "corpus weight", "compressed size of the shard's disagreement file"),
    MapMode("spec", "spec density", "share of the shard the spec calls allocated"),
    MapMode("exact", "exact vs spec", "popcount(tool XOR spec) from the bitmaps"),
)


class ShardHighlighted(Message):
    def __init__(self, shard_id: int) -> None:
        self.shard_id = shard_id
        super().__init__()


class ShardChosen(Message):
    def __init__(self, shard_id: int) -> None:
        self.shard_id = shard_id
        super().__init__()


class SpaceMap(Widget, can_focus=True):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up,k", "move(0,-1)", "up", show=False),
        Binding("down,j", "move(0,1)", "down", show=False),
        Binding("left,h", "move(-1,0)", "left", show=False),
        Binding("right,l", "move(1,0)", "right", show=False),
        Binding("home", "jump(0)", "first", show=False),
        Binding("end", "jump(255)", "last", show=False),
        Binding("enter", "choose", "open shard", show=False),
    ]

    cursor = reactive(0)
    mode_index = reactive(0)
    compact = reactive(False)

    @property
    def cell(self) -> str:
        return "█" if self.compact else "██"

    @property
    def cell_w(self) -> int:
        return 2 if self.compact else CELL_W

    def watch_compact(self, value: bool) -> None:
        self.refresh(layout=True)

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    def __init__(self, shards: list[Shard], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.shards = shards
        self.exact: dict[int, float] = {}
        self._by_id = {s.shard_id: s for s in shards}

    @property
    def mode(self) -> MapMode:
        return MODES[self.mode_index % len(MODES)]

    # fixed 16x16: a row is the shard id's high nibble, a column its low
    # nibble, so shard 0xab is row a, column b, covering 0xab000000...
    columns = COLS
    rows = 256 // COLS

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return GUTTER + COLS * self.cell_w

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return self.rows + 1

    def value_for(self, shard_id: int) -> float | None:
        shard = self._by_id.get(shard_id)
        mode = self.mode.key
        if mode == "exact":
            return self.exact.get(shard_id)
        if shard is None:
            return None
        if mode == "spread":
            return shard.spread()
        if mode == "spec":
            return shard.valid_counts.get("spec", 0) / shard.size
        if mode == "corpus":
            biggest = max((s.disagreement_bytes for s in self.shards), default=0)
            return (shard.disagreement_bytes / biggest) if biggest else 0.0
        return None

    def describe(self, shard_id: int) -> str:
        value = self.value_for(shard_id)
        shard = self._by_id.get(shard_id)
        if value is None:
            return "no data"
        if self.mode.key == "corpus":
            return human_bytes(shard.disagreement_bytes) if shard else "no data"
        if self.mode.key == "exact" and shard is not None:
            return f"{pct(value, 1)} ({abbreviate(value * shard.size)} words)"
        return pct(value, 1)

    def _style_for(self, shard_id: int) -> Style:
        value = self.value_for(shard_id)
        if value is None:
            return Style(color=EMPTY)
        index = min(int(value * (len(HEAT) - 1) + 0.5), len(HEAT) - 1)
        if value > 0 and index == 0:
            index = 1
        return Style(color=HEAT[index])

    def render_line(self, y: int) -> Strip:
        cell, cell_w = self.cell, self.cell_w
        width = GUTTER + COLS * cell_w
        if y == 0:
            segments = [Segment(" " * GUTTER, HEADER_STYLE)]
            for x in range(COLS):
                style = HEADER_STYLE + Style(bold=True) if x == self.cursor % COLS else HEADER_STYLE
                segments.append(Segment(f"{x:x}".ljust(cell_w), style))
            return Strip(segments, width)
        row = y - 1
        if row >= self.rows:
            return Strip.blank(self.size.width)
        style = LABEL_STYLE + Style(bold=True) if row == self.cursor // COLS else LABEL_STYLE
        # the cursor is bracketed rather than reverse-videoed: reversing a
        # solid block just repaints it in the background colour, which makes
        # the selected cell vanish exactly when it is a dark one.
        on_row = row == self.cursor // COLS
        col = self.cursor % COLS
        cursor_style = CURSOR_STYLE if self.has_focus else CURSOR_DIM
        segments = [
            Segment(f"{row:x} ", style),
            Segment("▕" if on_row and col == 0 else " ", cursor_style if on_row and col == 0 else style),
        ]
        for x in range(COLS):
            shard_id = row * COLS + x
            segments.append(Segment(cell, self._style_for(shard_id)))
            if on_row and x == col:
                segments.append(Segment("▏", cursor_style))
            elif on_row and x + 1 == col:
                segments.append(Segment("▕", cursor_style))
            else:
                segments.append(Segment(" ", Style()))
        return Strip(segments, width)

    def watch_cursor(self, value: int) -> None:
        self.post_message(ShardHighlighted(value))
        self.refresh()

    def watch_mode_index(self, value: int) -> None:
        self.refresh()
        self.post_message(ShardHighlighted(self.cursor))

    def action_move(self, dx: int, dy: int) -> None:
        cols = COLS
        target = self.cursor + dx + dy * cols
        if 0 <= target <= 255:
            self.cursor = target

    def action_jump(self, shard_id: int) -> None:
        self.cursor = max(0, min(255, shard_id))

    def action_choose(self) -> None:
        self.post_message(ShardChosen(self.cursor))

    def cycle_mode(self, step: int = 1) -> None:
        self.mode_index = (self.mode_index + step) % len(MODES)

    def on_click(self, event: events.Click) -> None:
        x = (int(event.x) - GUTTER) // self.cell_w
        y = int(event.y) - 1
        if 0 <= x < COLS and y >= 0:
            shard_id = y * COLS + x
            if 0 <= shard_id <= 255:
                self.focus()
                if shard_id == self.cursor:
                    self.post_message(ShardChosen(shard_id))
                else:
                    self.cursor = shard_id

    def legend(self) -> Text:
        text = Text()
        text.append("low ", style="#6b7683")
        for colour in HEAT:
            text.append("█", style=colour)
        text.append(" high", style="#6b7683")
        return text

    def ranked(self, limit: int = 8) -> list[tuple[int, float]]:
        scored = [
            (sid, value)
            for sid in range(256)
            if (value := self.value_for(sid)) is not None and value > 0
        ]
        scored.sort(key=lambda pair: -pair[1])
        return scored[:limit]
