from __future__ import annotations

from typing import ClassVar

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static

from . import bits


class HelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,q,question_mark", "dismiss", "close")
    ]

    def compose(self) -> ComposeResult:
        rows = (
            ("1 - 5", "overview / map / corpus / reproducers / goals"),
            ("tab, shift+tab", "cycle panes"),
            ("/ or w", "look up any 32-bit word across the corpus"),
            ("?", "this help"),
            ("r", "reload artifacts from disk"),
            ("q, ctrl+c", "quit"),
            ("", ""),
            ("map: arrows / hjkl", "move the shard cursor"),
            ("map: enter", "open that shard in the corpus browser"),
            ("map: m / M", "cycle the colour channel"),
            ("map: x", "compute exact bitmap disagreement (needs bitmaps/)"),
            ("", ""),
            ("corpus: s", "jump to a shard"),
            ("corpus: f", "cycle category filter (all / text tier / one category)"),
            ("corpus: n", "load the next page of records"),
            ("corpus: i", "index this shard (exact per-category counts)"),
            ("corpus: escape", "cancel a running scan"),
        )
        table = Table.grid(padding=(0, 3))
        table.add_column(width=20, style="#7fd1b9", no_wrap=True)
        table.add_column(style="#c5ced6")
        for key, description in rows:
            table.add_row(key, description)
        body = Group(
            Text("SILICA scope", style="bold #7fd1b9"),
            Text("a reader for an already-finished sweep - it never runs one", style="#6b7683"),
            Text(""),
            table,
            Text(""),
            Text("escape or q to close", style="#6b7683"),
        )
        with Vertical(id="help-box"):
            yield Static(body)


class LookupScreen(ModalScreen[int | None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="lookup-box"):
            yield Label("look up a 32-bit encoding", classes="card-title")
            yield Label("hex (0xd65f03c0 or d65f03c0), or 32 binary digits", id="lookup-hint")
            yield Input(placeholder="0xd65f03c0", id="lookup-input")
            yield Label("", id="lookup-error")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        word = bits.parse_word(event.value)
        if word is None:
            self.query_one("#lookup-error", Label).update(
                Text("not a 32-bit encoding", style="#e0335b")
            )
            return
        self.dismiss(word)


class ShardPrompt(ModalScreen[int | None]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="lookup-box"):
            yield Label("jump to shard", classes="card-title")
            yield Label("0 - 255, or a hex word like 0x109b485a", id="lookup-hint")
            yield Input(placeholder="16", id="shard-input")
            yield Label("", id="lookup-error")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        shard: int | None = None
        if raw.lower().startswith("0x"):
            word = bits.parse_word(raw)
            shard = None if word is None else word >> 24
        elif raw.isdigit():
            value = int(raw)
            shard = value if 0 <= value <= 255 else None
        if shard is None:
            self.query_one("#lookup-error", Label).update(
                Text("expected 0-255 or a 0x word", style="#e0335b")
            )
            return
        self.dismiss(shard)
