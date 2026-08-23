from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cairosvg

from silica_scope.app import ScopeApp
from silica_scope.widgets.spacemap import SpaceMap

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
ROOT = Path(sys.argv[2]).resolve()


def shot(app: ScopeApp, name: str) -> None:
    svg = app.export_screenshot()
    svg = svg.replace("Fira Code", "DejaVu Sans Mono")
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT / f"{name}.png"), scale=1.4)
    print("wrote", name)


async def settle(pilot, predicate, tries=400, delay=0.05):  # type: ignore[no-untyped-def]
    for _ in range(tries):
        await pilot.pause()
        await asyncio.sleep(delay)
        if predicate():
            return True
    return False


async def main() -> None:
    app = ScopeApp(ROOT)
    async with app.run_test(size=(140, 40)) as pilot:
        await asyncio.sleep(1.0)
        await pilot.pause()

        # corpus: load a second page, then index the shard exactly
        await pilot.press("3")
        await settle(pilot, lambda: len(app.records) >= 400)
        print("page 1:", len(app.records))
        await pilot.press("n")
        await settle(pilot, lambda: len(app.records) >= 800)
        print("page 2:", len(app.records))
        t = time.time()
        await pilot.press("i")
        ok = await settle(
            pilot,
            lambda: app.session.corpus is not None
            and app.session.corpus.cached_index(app.shard_id) is not None,
        )
        print("index ok:", ok, round(time.time() - t, 1), "s")
        idx = app.session.corpus.cached_index(app.shard_id)
        print("index:", idx)
        shot(app, "A-corpus-indexed")

        # map: exact bitmap channel
        await pilot.press("2")
        await pilot.pause()
        t = time.time()
        await pilot.press("x")
        ok = await settle(pilot, lambda: bool(app.query_one(SpaceMap).exact), tries=900)
        print("exact ok:", ok, round(time.time() - t, 1), "s")
        smap = app.query_one(SpaceMap)
        if smap.exact:
            worst = max(smap.exact.items(), key=lambda kv: kv[1])
            print("worst shard:", worst, "mean:", sum(smap.exact.values()) / len(smap.exact))
        shot(app, "B-map-exact")

        # move the cursor somewhere hot and open it
        for _ in range(13):
            await pilot.press("down")
        for _ in range(12):
            await pilot.press("right")
        await pilot.pause()
        shot(app, "C-map-cursor")
        await pilot.press("enter")
        await settle(pilot, lambda: bool(app.records))
        await asyncio.sleep(1.0)
        await pilot.pause()
        shot(app, "D-corpus-from-map")

        # reproducer -> corpus lookup
        await pilot.press("4")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        shot(app, "E-repro")
        await pilot.press("enter")
        await asyncio.sleep(1.5)
        await pilot.pause()
        shot(app, "F-repro-lookup")


asyncio.run(main())
