from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, discovery


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="silica-scope",
        description=(
            "Browse a finished SILICA sweep: per-tool metrics, the shard map, the "
            "disagreement corpus and the reproducers. Reads published artifacts only - "
            "it never runs a sweep."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help=(
            "artifacts directory (default: $SILICA_ARTIFACTS, else the nearest "
            "artifacts/ from the current directory)"
        ),
    )
    parser.add_argument("--goals", default=None, help="path to GOALS.yml")
    parser.add_argument(
        "--report",
        action="store_true",
        help="print a static summary instead of starting the TUI",
    )
    parser.add_argument("--version", action="version", version=f"silica-scope {__version__}")
    return parser


def report(root: Path, goals_file: Path | None) -> int:
    from rich.console import Console

    from . import views
    from .session import load

    # piped output defaults to 80 columns, which truncates the tool names and
    # the percentages down to ellipses. the report has a floor.
    probe = Console()
    console = Console(width=max(probe.width, 104))
    session = load(root, goals_file)
    if not session.has_anything:
        console.print(
            f"[#e0a03a]no SILICA artifacts under[/] {root}\n"
            "point it at one: [#7fd1b9]silica-scope /path/to/artifacts[/]"
        )
        return 1
    problems = views.problems_panel(session)
    if problems is not None:
        console.print(problems)
        console.print()
    console.print(views.headline(session))
    console.print()
    console.print(views.tool_table(session))
    console.print()
    console.print(views.category_table(session))
    console.print()
    console.print(views.provenance(session))
    console.print()
    console.print(views.goals_table(session))
    console.print(views.goals_note())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = discovery.locate(args.path)
    goals_file = Path(args.goals).expanduser().resolve() if args.goals else None
    if goals_file is not None and not goals_file.is_file():
        print(f"silica-scope: no such file: {goals_file}", file=sys.stderr)
        return 2
    if args.report:
        return report(root, goals_file)
    from .app import ScopeApp

    ScopeApp(root, goals_file).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
