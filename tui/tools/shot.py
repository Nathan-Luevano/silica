from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cairosvg

from silica_scope.app import ScopeApp

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
OUT.mkdir(parents=True, exist_ok=True)
ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("artifacts")
SIZE = (140, 40)
if len(sys.argv) > 3:
    w, h = sys.argv[3].split("x")
    SIZE = (int(w), int(h))

SCRIPT: list[tuple[str, list[str], float]] = [
    ("01-overview", [], 0.3),
    ("02-map", ["2"], 0.4),
    ("03-map-mode", ["m"], 0.3),
    ("04-corpus", ["3"], 2.5),
    ("05-corpus-filter", ["f"], 2.5),
    ("06-repro", ["4"], 0.4),
    ("07-goals", ["5"], 0.4),
    ("08-help", ["question_mark"], 0.4),
    ("09-lookup", ["escape", "slash"], 0.4),
]


async def main() -> None:
    app = ScopeApp(ROOT.resolve())
    async with app.run_test(size=SIZE) as pilot:
        for name, keys, delay in SCRIPT:
            for key in keys:
                await pilot.press(key)
            await pilot.pause()
            await asyncio.sleep(delay)
            await pilot.pause()
            svg = app.export_screenshot()
            (OUT / f"{name}.svg").write_text(svg)
            # cairosvg has no Fira Code; DejaVu Sans Mono is the one font on
            # this box that actually has the block and box-drawing glyphs, so
            # the PNG shows what a real terminal shows instead of tofu.
            svg = svg.replace("Fira Code", "DejaVu Sans Mono").replace(
                "font-family:", "font-family:DejaVu Sans Mono,"
            )
            cairosvg.svg2png(
                bytestring=svg.encode(), write_to=str(OUT / f"{name}.png"), scale=1.4
            )
            print("wrote", name)


asyncio.run(main())
