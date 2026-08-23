from __future__ import annotations

from math import log10

from rich.align import Align
from rich.box import HEAVY, SIMPLE
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import bits
from .corpus import Record, is_placeholder
from .fmt import commas, fine_bar, human_bytes, pct, truncate
from .model import ORACLES, TOTAL_WORDS, Goal, Reproducer, Shard
from .session import Session

ACCENT = "bold #7fd1b9"
DIM = "#6b7683"
WARN = "#e0a03a"
BAD = "#e0335b"
GOOD = "#5fbf6a"

CATEGORY_STYLE = {
    "VALIDITY": "#7fae24",
    "MNEMONIC": "#e0a03a",
    "OPERAND": "#5aa9e6",
    "ALIAS": "#b07fd1",
    "FORMATTING": "#7fd1b9",
    "NORMALIZATION_UNCERTAIN": "#d4741d",
    "CRASH": "#e0335b",
}


SHORT_CATEGORY = {"NORMALIZATION_UNCERTAIN": "NORM_UNCERTAIN"}


def category_text(name: str, short: bool = False) -> Text:
    label = SHORT_CATEGORY.get(name, name) if short else name
    return Text(label, style=CATEGORY_STYLE.get(name, "white"))


def clean(text: str) -> str:
    # llvm hands back "adr\tx26, #-825080"; a raw tab in a table cell blows
    # the column apart.
    return " ".join(text.split())


def _kv(label: str, value: RenderableType, label_width: int = 22) -> Table:
    t = Table.grid(padding=(0, 1))
    t.add_column(width=label_width, style=DIM, no_wrap=True)
    t.add_column(ratio=1)
    t.add_row(label, value)
    return t


def missing_panel(what: str, path: str, why: str) -> Panel:
    body = Group(
        Text(why, style=DIM),
        Text(""),
        Text("looked for", style=DIM),
        Text(path, style="italic #8fa3b0"),
    )
    return Panel(body, title=f"[{WARN}]no {what}[/]", border_style=WARN, box=SIMPLE, padding=(1, 2))


def headline(session: Session) -> RenderableType:
    g1_alloc = session.g1_value("allocated")
    g1_unalloc = session.g1_value("unallocated")
    total_dis = session.g4_int("total_disagreements")
    tiles: list[RenderableType] = []

    def tile(value: str, label: str, style: str, note: str = "") -> Panel:
        inner = Group(
            Align.center(Text(value, style=style)),
            Align.center(Text(label, style=DIM)),
            *([Align.center(Text(note, style=DIM))] if note else []),
        )
        return Panel(inner, box=SIMPLE, padding=(0, 1), border_style="#2b3138")

    if session.has_sweep_evidence:
        tiles.append(
            tile("4,294,967,296", "encodings swept", "bold #7fd1b9", "all 2^32, not sampled")
        )
    else:
        tiles.append(
            tile("--", "encodings swept", "bold #6b7683", "no sweep artifacts here")
        )
    if isinstance(g1_alloc, int) and isinstance(g1_unalloc, int):
        tiles.append(
            tile(
                commas(g1_alloc),
                "allocated per spec",
                "bold #7fae24",
                f"{pct(g1_alloc / TOTAL_WORDS, 1)} of the space",
            )
        )
    if total_dis is not None:
        tiles.append(
            tile(
                commas(total_dis),
                "disagreements",
                f"bold {BAD}",
                f"{pct(total_dis / TOTAL_WORDS, 1)} of the space",
            )
        )
    tiles.append(
        tile(str(len(session.reproducers)), "reproducers", "bold #5aa9e6", "minimal, filing-ready")
    )
    grid = Table.grid(expand=True)
    for _ in tiles:
        grid.add_column(ratio=1)
    grid.add_row(*tiles)
    return grid


def tool_table(session: Session) -> RenderableType:
    if not session.metrics.ok:
        return missing_panel(
            "per-tool metrics",
            str(session.artifacts.path("metrics")),
            session.metrics.error or "report/metrics.json could not be read",
        )
    metrics = session.metrics.value
    table = Table(box=SIMPLE, expand=True, pad_edge=False, header_style=DIM)
    table.add_column("", width=2, no_wrap=True)
    table.add_column("tool", style="bold", no_wrap=True)
    table.add_column("agrees", justify="right", no_wrap=True)
    table.add_column("", no_wrap=True)
    table.add_column("disagreeing words", justify="right", no_wrap=True)
    table.add_column("text tier", justify="right", no_wrap=True)
    order = metrics.ranking_worst_first or sorted(metrics.per_tool)
    for rank, name in enumerate(order, start=1):
        tool = metrics.per_tool.get(name)
        if tool is None:
            continue
        rate = tool.validity_agreement_micro
        # one absolute 0-100% scale for all three, agreement then the
        # remainder in red - a rank-relative scale would make an 84% tool
        # look like it failed and an 88% one look perfect.
        bar = Text(fine_bar(rate, 26).replace("·", ""), style=GOOD)
        bar.append("▒" * (26 - len(bar.plain)), style=BAD)
        table.add_row(
            Text(f"{rank}.", style=DIM),
            name,
            Text(pct(rate), style="#c5ced6"),
            bar,
            commas(tool.validity_disagreements),
            Text(pct(tool.text_agreement_micro), style=DIM),
        )
    caption = Text()
    any_tool = next(iter(metrics.per_tool.values()), None)
    if any_tool is not None:
        caption.append("ranked worst first. ", style=DIM)
        caption.append(
            f"text tier is {any_tool.text_method}: "
            f"{commas(any_tool.text_sample_size)} of {commas(any_tool.text_population)}",
            style=DIM,
        )
    return Group(table, caption)


def category_table(session: Session) -> RenderableType:
    counts = session.category_counts()
    if not counts:
        return missing_panel(
            "taxonomy breakdown",
            str(session.artifacts.path("g4_metrics")),
            session.g4.error or "g4_metrics.json has no category_counts",
        )
    total = sum(counts.values()) or 1
    biggest = max(counts.values()) or 1
    table = Table(box=SIMPLE, expand=True, pad_edge=False, header_style=DIM)
    table.add_column("category", no_wrap=True)
    table.add_column("count", justify="right", no_wrap=True)
    table.add_column("share", justify="right", no_wrap=True)
    table.add_column("", no_wrap=True)
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        share = count / total
        # log scale: VALIDITY outnumbers OPERAND 800:1, so a linear bar -
        # against the total or against the largest - draws every other
        # category as a blank line. orders of magnitude is the real story.
        scale = log10(count + 1) / log10(biggest + 1) if biggest else 0.0
        table.add_row(
            category_text(name),
            commas(count),
            pct(share, 3),
            Text(fine_bar(scale, 18), style=CATEGORY_STYLE.get(name, "white")),
        )
    return Group(table, Text("bar length is log10(count) - the counts span 4 decades", style=DIM))


def provenance(session: Session) -> RenderableType:
    rows: list[RenderableType] = []
    rows.append(_kv("spec release", Text(session.spec_release, style="#8fa3b0")))
    if session.result_hash.value:
        style = "#8fa3b0" if session.result_hash.ok else WARN
        rows.append(_kv("result hash", Text(str(session.result_hash.value), style=style)))
        if session.result_hash.error:
            rows.append(_kv("", Text(session.result_hash.error, style=WARN)))
    else:
        rows.append(_kv("result hash", Text(session.result_hash.error or "absent", style=WARN)))
    method = None
    if session.g4.ok and isinstance(session.g4.value, dict):
        method = session.g4.value.get("text_tier_method")
        exhaustive = session.g4.value.get("validity_tier_exhaustive")
        rows.append(
            _kv(
                "validity tier",
                Text(
                    "exhaustive - all 2^32 words" if exhaustive else "NOT exhaustive",
                    style=GOOD if exhaustive else BAD,
                ),
            )
        )
        pop = session.g4_int("text_tier_population")
        size = session.g4_int("text_tier_sample_size")
        if method == "sampled" and pop and size:
            rows.append(
                _kv(
                    "text tier",
                    Text(
                        f"sampled - {commas(size)} of {commas(pop)} ({pct(size / pop, 3)})",
                        style=WARN,
                    ),
                )
            )
        elif method:
            rows.append(_kv("text tier", Text(str(method), style="#8fa3b0")))
    shards_with = session.g4_int("shards_with_disagreements")
    if shards_with is not None:
        rows.append(_kv("shards with corpus", Text(f"{shards_with} of 256", style="#8fa3b0")))
    rows.append(_kv("artifacts root", Text(str(session.root), style="italic #6b7683")))
    return Group(*rows)


def goals_table(session: Session) -> RenderableType:
    if not session.goals:
        return missing_panel(
            "goals file",
            str(session.artifacts.presence["goals"].path),
            session.goals_error or "GOALS.yml not found next to the artifacts root",
        )
    table = Table(box=SIMPLE, expand=True, pad_edge=False, header_style=DIM)
    table.add_column("goal", style="bold", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("statement", ratio=1)
    for goal in session.goals:
        table.add_row(goal.id, status_text(goal), Text(goal.statement, style="#c5ced6"))
    return table


def status_text(goal: Goal) -> Text:
    status = goal.status.lower()
    if status in ("pass", "verified", "passed"):
        return Text("PASS", style=f"bold {GOOD}")
    if status in ("fail", "failed"):
        return Text("FAIL", style=f"bold {BAD}")
    return Text(goal.status or "unknown", style=WARN)


def goals_note() -> Text:
    return Text(
        "status is written by the verifiers, never by hand - run `silica verify` to refresh",
        style=DIM,
    )


def shard_detail(
    shard: Shard | None, shard_id: int, session: Session, compact: bool = False
) -> RenderableType:
    if shard is None:
        return Group(
            Text(f"shard {shard_id:03d}", style=ACCENT),
            Text(""),
            Text("no shard record on disk", style=WARN),
            Text(str(session.artifacts.path("shards") / f"{shard_id:03d}.json"), style=DIM),
        )
    start, end = shard.start, shard.end
    rows: list[RenderableType] = [
        Text(f"shard {shard.label}", style=ACCENT),
        _kv("word range", Text(f"0x{start:08x} .. 0x{end - 1:08x}", style="#8fa3b0"), 14),
        _kv("status", Text(shard.status, style=GOOD if shard.status == "complete" else BAD), 14),
        _kv("sweep time", Text(_ms(shard.duration_ms), style="#8fa3b0"), 14),
        _kv(
            "crashes",
            Text(
                f"{shard.crash_count} ({shard.untriaged_crash_count} untriaged)",
                style=BAD if shard.crash_count else DIM,
            ),
            14,
        ),
    ]
    table = Table(box=SIMPLE, expand=True, pad_edge=False, header_style=DIM)
    table.add_column("oracle", width=8, no_wrap=True)
    table.add_column("valid", justify="right", no_wrap=True)
    table.add_column("", justify="right", width=7, no_wrap=True)
    if not compact:
        table.add_column("", width=16, no_wrap=True)
    for oracle in ORACLES:
        count = shard.valid_counts.get(oracle)
        if count is None:
            table.add_row(oracle, Text("-", style=DIM), "", *([] if compact else [""]))
            continue
        frac = count / shard.size
        style = ACCENT if oracle == "spec" else "#c5ced6"
        cells: list[RenderableType] = [
            Text(oracle, style=style),
            commas(count),
            Text(pct(frac, 1), style=DIM),
        ]
        if not compact:
            cells.append(
                Text(fine_bar(frac, 16), style="#5aa9e6" if oracle != "spec" else "#7fd1b9")
            )
        table.add_row(*cells)
    rows.append(Text(""))
    rows.append(table)
    rows.append(
        _kv(
            "corpus",
            Text(
                human_bytes(shard.disagreement_bytes) + " compressed"
                if shard.has_corpus
                else "no disagreements recorded",
                style="#8fa3b0" if shard.has_corpus else DIM,
            ),
            14,
        )
    )
    rows.append(_kv("content hash", Text(shard.content_hash[:32] + "…", style=DIM), 14))
    if shard.has_corpus:
        rows.append(Text(""))
        rows.append(Text("enter  browse this shard's disagreements", style=DIM))
    return Group(*rows)


def _ms(ms: int) -> str:
    from .fmt import duration

    return duration(ms)


def word_view(
    word: int, record: Record | None, note: str = "", compact: bool = False
) -> RenderableType:
    grouped, ruler = bits.bit_rows(word)
    head = Table.grid(padding=(0, 2))
    head.add_column(no_wrap=True)
    head.add_column(no_wrap=True)
    head.add_row(Text(bits.hex_word(word), style="bold #7fd1b9"), Text(bits.group_of(word), style=DIM))
    body: list[RenderableType] = [
        head,
        Text(grouped, style="#c5ced6"),
        Text(ruler, style=DIM),
        _kv("little-endian", Text(bits.bytes_le(word), style="#8fa3b0"), 14),
        _kv("shard", Text(f"{word >> 24:03d}", style="#8fa3b0"), 14),
    ]
    if record is None:
        body.append(Text(""))
        body.append(Text(note or "no disagreement record for this word", style=DIM))
        return Group(*body)
    body.append(_kv("category", category_text(record.category), 14))
    table = Table(box=SIMPLE, expand=True, pad_edge=False, header_style=DIM)
    table.add_column("oracle", width=8, no_wrap=True)
    table.add_column("valid", width=6, no_wrap=True)
    table.add_column("text", ratio=1, overflow="fold" if compact else "ellipsis")
    raw_spec = record.oracle_text.get("spec")
    spec_text = "" if is_placeholder(raw_spec) else clean(raw_spec or "")
    for oracle in ORACLES:
        valid = record.oracle_valid.get(oracle)
        text = clean(record.oracle_text.get(oracle) or "") or None
        if valid is None:
            valid_cell = Text("?", style=DIM)
        else:
            valid_cell = Text("valid" if valid else "reject", style=GOOD if valid else BAD)
        if is_placeholder(text):
            rendered = Text("valid, no text captured" if valid else "-", style=DIM)
        else:
            rendered = Text(text or "-", style="#c5ced6")
            if oracle != "spec" and text and spec_text and text != spec_text:
                rendered = Text(text, style=WARN)
        table.add_row(
            Text(oracle, style=ACCENT if oracle == "spec" else "#c5ced6"), valid_cell, rendered
        )
    body.append(Text(""))
    body.append(table)
    diverging = record.disagreeing_tools()
    if diverging:
        body.append(
            Text("differs from spec: " + ", ".join(diverging), style=WARN)
        )
    return Group(*body)


def reproducer_detail(repro: Reproducer) -> RenderableType:
    rows: list[RenderableType] = [
        Text(repro.path.name, style=ACCENT),
        _kv("word", Text(repro.word, style="bold #7fd1b9"), 12),
        _kv("category", category_text(repro.category), 12),
        _kv("tool", Text(repro.tool, style="#5aa9e6"), 12),
        Text(""),
        _kv("spec says", Text(repro.spec or "-", style=GOOD), 12),
        _kv("tool says", Text(repro.actual or "-", style=BAD), 12),
    ]
    word = repro.word_int
    if word is not None:
        grouped, ruler = bits.bit_rows(word)
        rows.extend([Text(""), Text(grouped, style="#c5ced6"), Text(ruler, style=DIM)])
    if repro.problems:
        rows.append(Text(""))
        for problem in repro.problems:
            rows.append(Text("! " + problem, style=BAD))
    if repro.body:
        rows.extend([Text(""), Rule(style="#2b3138"), Text(repro.body, style="#c5ced6")])
    return Group(*rows)


def problems_panel(session: Session) -> RenderableType | None:
    problems = session.problems()
    if not problems:
        return None
    body = Group(*[Text("• " + truncate(p, 200), style=WARN) for p in problems[:8]])
    title = f"[{WARN}]{len(problems)} artifact problem{'s' if len(problems) != 1 else ''}[/]"
    return Panel(body, title=title, border_style=WARN, box=HEAVY, padding=(0, 1))
