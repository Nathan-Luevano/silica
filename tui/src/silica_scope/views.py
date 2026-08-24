from __future__ import annotations

from math import log10

from rich.align import Align
from rich.box import Box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import bits
from .corpus import Record, is_placeholder
from .fmt import commas, fine_bar, human_bytes, pct, truncate
from .model import ORACLES, TOTAL_WORDS, Reproducer, Shard
from .session import Session

# two colours, used sparingly, plus the two greys that make up the plain
# foreground/dim value scale. FG/DIM are shades, not hues, so they don't
# count against the "2-3 colours total" budget - ACCENT and BAD are the
# whole budget. ACCENT marks anything interactive, selected, or singled
# out as the reference value (the spec oracle's own row). BAD is reserved
# for a real problem: a malformed artifact, a crashed shard, a FAILing
# goal, an actual disagreement with the spec oracle - never for an
# ordinary "invalid" verdict, which is completely routine data.
ACCENT_COLOR = "#8fd6c4"
ACCENT = f"bold {ACCENT_COLOR}"
FG = "#c8c8c8"
DIM = "#808080"
BAD = "#e0335b"

# ascii box: '+', '-', '|' - the same table rules as before, drawn without
# unicode box-drawing characters. only the header rule differs from an
# invisible SIMPLE box; everything else is blank, matching the plain,
# borderless look tables had before.
SIMPLE_ASCII = Box("    \n    \n -- \n    \n    \n -- \n    \n    \n")
ASCII_BOX = Box("+--+\n| ||\n|-+|\n| ||\n|-+|\n|-+|\n| ||\n+--+\n")


SHORT_CATEGORY = {"NORMALIZATION_UNCERTAIN": "NORM_UNCERTAIN"}


def category_text(name: str, short: bool = False) -> Text:
    # no per-category colour coding - the label itself is the distinguisher.
    return Text(SHORT_CATEGORY.get(name, name) if short else name, style=FG)


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
    # a missing artifact is a normal state (a published checkout ships only
    # reproducers/ and result_hash.txt by design), not a problem - plain,
    # not red. session.problems() is where a genuinely malformed file gets
    # flagged, in BAD, via problems_panel below.
    body = Group(
        Text(why, style=DIM),
        Text(""),
        Text("looked for", style=DIM),
        Text(path, style="italic " + DIM),
    )
    return Panel(
        body, title=f"no {what}", title_align="left", border_style=DIM, box=ASCII_BOX, padding=(1, 2)
    )


def headline(session: Session) -> RenderableType:
    g1_alloc = session.g1_value("allocated")
    g1_unalloc = session.g1_value("unallocated")
    total_dis = session.g4_int("total_disagreements")
    tiles: list[RenderableType] = []

    def tile(value: str, label: str, style: str, note: str = "", note_style: str = DIM) -> Panel:
        inner = Group(
            Align.center(Text(value, style=style)),
            Align.center(Text(label, style=DIM)),
            *([Align.center(Text(note, style=note_style))] if note else []),
        )
        return Panel(inner, box=SIMPLE_ASCII, padding=(0, 1), border_style="#2a2a2a")

    if session.has_sweep_evidence:
        tiles.append(tile("4,294,967,296", "encodings swept", f"bold {FG}", "all 2^32, not sampled"))
    else:
        tiles.append(tile("--", "encodings swept", DIM, "no sweep artifacts here"))
    if isinstance(g1_alloc, int) and isinstance(g1_unalloc, int):
        tiles.append(
            tile(
                commas(g1_alloc),
                "allocated per spec",
                f"bold {FG}",
                f"{pct(g1_alloc / TOTAL_WORDS, 1)} of the space",
            )
        )
    if total_dis is not None:
        # the one headline number that earns red: it is the count of real
        # tool-vs-spec disagreements, the actual defects this project exists
        # to surface, not a UI problem but the substantive finding.
        tiles.append(
            tile(
                commas(total_dis),
                "disagreements",
                f"bold {BAD}",
                f"{pct(total_dis / TOTAL_WORDS, 1)} of the space",
            )
        )
    readable = [r for r in session.reproducers if r.word or r.body]
    unreadable = len(session.reproducers) - len(readable)
    tiles.append(
        tile(
            str(len(readable)),
            "reproducers",
            f"bold {FG}",
            f"{unreadable} unreadable" if unreadable else "minimal, filing-ready",
            note_style=BAD if unreadable else DIM,
        )
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
    table = Table(box=SIMPLE_ASCII, expand=True, pad_edge=False, header_style=DIM)
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
        # one absolute 0-100% scale for all three, so an 84% tool and an 88%
        # one are visibly close instead of looking like fail/perfect under a
        # rank-relative scale. one colour for the filled portion, no
        # green-to-red gradient - the number carries the precision.
        width = 26
        filled = round(rate * width)
        bar = Text("#" * filled, style=ACCENT)
        bar.append("-" * (width - filled), style=DIM)
        table.add_row(
            Text(f"{rank}.", style=DIM),
            name,
            Text(pct(rate), style=FG),
            bar,
            commas(tool.validity_disagreements),
            Text(pct(tool.text_agreement_micro), style=DIM),
        )
    caption = Text("ranked worst first.", style=DIM)
    methods = {
        (t.text_method, t.text_sample_size, t.text_population) for t in metrics.per_tool.values()
    }
    if len(methods) == 1:
        method, sample, population = next(iter(methods))
        caption.append(
            f" text tier is {method}: {commas(sample)} of {commas(population)}", style=DIM
        )
    elif methods:
        # never fold differing per-tool denominators into one claim.
        caption.append(" text tier denominators differ per tool - see each row", style=DIM)
    note = Text(
        "text-tier disagreements are corpus record counts, not per-tool measurements",
        style=DIM,
    )
    return Group(table, caption, note)


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
    table = Table(box=SIMPLE_ASCII, expand=True, pad_edge=False, header_style=DIM)
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
            Text(fine_bar(scale, 18), style=ACCENT),
        )
    return Group(table, Text("bar length is log10(count) - the counts span 4 decades", style=DIM))


def provenance(session: Session) -> RenderableType:
    rows: list[RenderableType] = []
    rows.append(_kv("spec release", Text(session.spec_release, style=FG)))
    if session.result_hash.value:
        # a malformed hash means a corrupted artifact - a real problem.
        style = FG if session.result_hash.ok else BAD
        rows.append(_kv("result hash", Text(str(session.result_hash.value), style=style)))
        if session.result_hash.error:
            rows.append(_kv("", Text(session.result_hash.error, style=BAD)))
    else:
        rows.append(_kv("result hash", Text(session.result_hash.error or "absent", style=BAD)))
    method = None
    if session.g4.ok and isinstance(session.g4.value, dict):
        method = session.g4.value.get("text_tier_method")
        exhaustive = session.g4.value.get("validity_tier_exhaustive")
        rows.append(
            _kv(
                "validity tier",
                Text(
                    # exhaustive is the expected, unremarkable state - plain.
                    # "NOT exhaustive" would undercut the project's central
                    # claim, which is a real problem worth flagging.
                    "exhaustive - all 2^32 words" if exhaustive else "NOT exhaustive",
                    style=FG if exhaustive else BAD,
                ),
            )
        )
        pop = session.g4_int("text_tier_population")
        size = session.g4_int("text_tier_sample_size")
        if method == "sampled" and pop and size:
            rows.append(
                _kv(
                    "text tier",
                    Text(f"sampled - {commas(size)} of {commas(pop)} ({pct(size / pop, 3)})", style=FG),
                )
            )
        elif method:
            rows.append(_kv("text tier", Text(str(method), style=FG)))
    shards_with = session.g4_int("shards_with_disagreements")
    if shards_with is not None:
        rows.append(_kv("shards with corpus", Text(f"{shards_with} of 256", style=FG)))
    rows.append(_kv("artifacts root", Text(str(session.root), style="italic " + DIM)))
    return Group(*rows)


def shard_detail(
    shard: Shard | None, shard_id: int, session: Session, compact: bool = False
) -> RenderableType:
    if shard is None:
        return Group(
            Text(f"shard {shard_id:03d}", style=ACCENT),
            Text(""),
            Text("no shard record on disk", style=BAD),
            Text(str(session.artifacts.path("shards") / f"{shard_id:03d}.json"), style=DIM),
        )
    start, end = shard.start, shard.end
    label_w = 11 if compact else 14
    rows: list[RenderableType] = [
        Text(f"shard {shard.label}", style=ACCENT),
        _kv("words", Text(f"0x{start:08x}..0x{end - 1:08x}", style=FG), label_w),
        _kv(
            "status",
            # "complete" is the expected, unremarkable state - plain; only a
            # non-complete shard (crashed, incomplete) is worth flagging red.
            Text(shard.status, style=FG if shard.status == "complete" else BAD),
            label_w,
        ),
        _kv("sweep time", Text(_ms(shard.duration_ms), style=FG), label_w),
        _kv(
            "crashes",
            Text(
                f"{shard.crash_count} ({shard.untriaged_crash_count} untriaged)",
                style=BAD if shard.crash_count else DIM,
            ),
            label_w,
        ),
    ]
    table = Table(box=SIMPLE_ASCII, expand=True, pad_edge=False, header_style=DIM)
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
        style = ACCENT if oracle == "spec" else FG
        cells: list[RenderableType] = [
            Text(oracle, style=style),
            commas(count),
            Text(pct(frac, 1), style=DIM),
        ]
        if not compact:
            cells.append(Text(fine_bar(frac, 16), style=ACCENT))
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
                style=FG if shard.has_corpus else DIM,
            ),
            label_w,
        )
    )
    rows.append(
        _kv(
            "content hash",
            Text(shard.content_hash[: 16 if compact else 32] + "...", style=DIM),
            label_w,
        )
    )
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
    head.add_row(Text(bits.hex_word(word), style=ACCENT), Text(bits.group_of(word), style=DIM))
    body: list[RenderableType] = [
        head,
        Text(grouped, style=FG),
        Text(ruler, style=DIM),
        _kv("little-endian", Text(bits.bytes_le(word), style=FG), 14),
        _kv("shard", Text(f"{word >> 24:03d}", style=FG), 14),
    ]
    if record is None:
        body.append(Text(""))
        for line in (note or "no disagreement record for this word").splitlines():
            body.append(Text(line, style=DIM))
        return Group(*body)
    body.append(_kv("category", category_text(record.category), 14))
    table = Table(box=SIMPLE_ASCII, expand=True, pad_edge=False, header_style=DIM)
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
            # "valid" and "reject" are both ordinary data - most of the
            # space is legitimately UNALLOCATED - so neither gets colour.
            valid_cell = Text("valid" if valid else "reject", style=FG)
        if is_placeholder(text):
            rendered = Text("valid, no text captured" if valid else "-", style=DIM)
        else:
            rendered = Text(text or "-", style=FG)
            if oracle != "spec" and text and spec_text and text != spec_text:
                rendered = Text(text, style=BAD)
        table.add_row(
            Text(oracle, style=ACCENT if oracle == "spec" else FG), valid_cell, rendered
        )
    body.append(Text(""))
    body.append(table)
    validity_diff = record.validity_disagreements()
    if validity_diff:
        body.append(Text("validity differs from spec: " + ", ".join(validity_diff), style=BAD))
    elif spec_text:
        # deliberately not a list of "tools whose text differs": the spec
        # oracle emits a bare mnemonic, so a raw string compare marks every
        # tool on every text-tier record. the record's own `category` is the
        # sweep's normalized verdict; this reader does not second-guess it.
        body.append(
            Text(
                f"all four agree on validity; the sweep classified the text as {record.category}",
                style=DIM,
            )
        )
    return Group(*body)


def reproducer_detail(repro: Reproducer) -> RenderableType:
    rows: list[RenderableType] = [
        Text(repro.path.name, style=ACCENT),
        _kv("word", Text(repro.word, style=ACCENT), 12),
        _kv("category", category_text(repro.category), 12),
        _kv("tool", Text(repro.tool, style=FG), 12),
        Text(""),
        # spec is the reference, plain; the tool's line is the one being
        # filed as wrong, so it is the one that earns red.
        _kv("spec says", Text(repro.spec or "-", style=FG), 12),
        _kv("tool says", Text(repro.actual or "-", style=BAD), 12),
    ]
    word = repro.word_int
    if word is not None:
        grouped, ruler = bits.bit_rows(word)
        rows.extend([Text(""), Text(grouped, style=FG), Text(ruler, style=DIM)])
    if repro.problems:
        rows.append(Text(""))
        for problem in repro.problems:
            rows.append(Text("! " + problem, style=BAD))
    if repro.body:
        rows.extend([Text(""), Rule(style="#2a2a2a", characters="-"), Text(repro.body, style=FG)])
    return Group(*rows)


def problems_panel(session: Session) -> RenderableType | None:
    problems = session.problems()
    if not problems:
        return None
    body = Group(*[Text("- " + truncate(p, 200), style=BAD) for p in problems[:8]])
    title = f"[{BAD}]{len(problems)} artifact problem{'s' if len(problems) != 1 else ''}[/]"
    return Panel(body, title=title, title_align="left", border_style=BAD, box=ASCII_BOX, padding=(0, 1))
