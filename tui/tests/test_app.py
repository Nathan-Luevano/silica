from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import DataTable, ListView, Static, TabbedContent

from silica_scope.app import ScopeApp
from silica_scope.widgets.spacemap import SpaceMap


async def settle(pilot, predicate, tries: int = 80) -> bool:  # type: ignore[no-untyped-def]
    for _ in range(tries):
        await pilot.pause()
        if predicate():
            return True
    return False


def flat_text(app: ScopeApp) -> str:
    # the detail panes wrap, so a phrase that must appear can be split across
    # lines; collapse whitespace before asserting on prose.
    return " ".join(screen_text(app).split())


def screen_text(app: ScopeApp) -> str:
    screen = app.screen
    return "\n".join(
        strip.text
        for strip in screen._compositor.render_strips(screen.size)
    )


@pytest.mark.asyncio
async def test_every_pane_renders_against_real_shaped_artifacts(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, full_artifacts.parent / "GOALS.yml")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, lambda: "capstone" in screen_text(app))
        text = screen_text(app)
        assert "encodings swept" in text
        assert "capstone" in text
        for key, marker in (
            ("2", "shard 000"),
            ("4", "0x109b485a"),
            ("5", "Parse the ARM ISA XML"),
        ):
            await pilot.press(key)
            await pilot.pause()
            assert marker in screen_text(app), f"pane {key} did not render {marker!r}"


@pytest.mark.asyncio
async def test_corpus_pane_streams_records(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("3")
        await settle(pilot, lambda: len(app.records) == 3)
        assert [r.hex for r in app.records] == ["0x00000000", "0x00000001", "0x00000002"]
        assert app.query_one("#corpus-table", DataTable).row_count == 3


@pytest.mark.asyncio
async def test_filter_cycles_and_narrows(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("3")
        await settle(pilot, lambda: len(app.records) == 3)
        await pilot.press("f")  # text tier only
        await settle(pilot, lambda: app.filter_label == "text tier only" and len(app.records) == 1)
        assert app.filter_label == "text tier only"
        assert [r.category for r in app.records] == ["OPERAND"]


@pytest.mark.asyncio
async def test_map_cursor_moves_and_updates_detail(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        smap = app.query_one(SpaceMap)
        assert smap.cursor == 0
        await pilot.press("right")
        await pilot.pause()
        assert smap.cursor == 1
        await pilot.press("down")
        await pilot.pause()
        assert smap.cursor == 17
        assert "shard 017" in screen_text(app)
        # shard 17 has no record on disk in this fixture
        assert "no shard record on disk" in screen_text(app)


@pytest.mark.asyncio
async def test_map_cursor_stays_in_bounds(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        smap = app.query_one(SpaceMap)
        for _ in range(4):
            await pilot.press("left")
            await pilot.press("up")
            await pilot.pause()
        assert smap.cursor == 0
        await pilot.press("end")
        await pilot.pause()
        assert smap.cursor == 255
        for _ in range(4):
            await pilot.press("right")
            await pilot.press("down")
            await pilot.pause()
        assert smap.cursor == 255


@pytest.mark.asyncio
async def test_map_channel_cycles(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        first = app.query_one(SpaceMap).mode.key
        await pilot.press("m")
        await pilot.pause()
        assert app.query_one(SpaceMap).mode.key != first


@pytest.mark.asyncio
async def test_word_lookup_finds_and_misses(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("slash")
        await pilot.pause()
        for ch in "0x00000002":
            await pilot.press(ch if ch != "x" else "x")
        await pilot.press("enter")
        await settle(pilot, lambda: "OPERAND" in screen_text(app))
        text = screen_text(app)
        assert "0x00000002" in text
        assert "OPERAND" in text


@pytest.mark.asyncio
async def test_word_lookup_says_so_when_a_shard_has_no_corpus(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("slash")
        await pilot.pause()
        for ch in "0x7f000000":
            await pilot.press(ch)
        await pilot.press("enter")
        await settle(pilot, lambda: "no disagreements at all" in screen_text(app))
        assert "no disagreements at all" in screen_text(app)


@pytest.mark.asyncio
async def test_bad_word_is_rejected_without_closing_the_prompt(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("slash")
        await pilot.pause()
        for ch in "zzz":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        assert "not a 32-bit encoding" in screen_text(app)


@pytest.mark.asyncio
async def test_help_opens_and_closes(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        assert "look up any 32-bit word" in screen_text(app)
        await pilot.press("escape")
        await pilot.pause()
        assert "look up any 32-bit word" not in screen_text(app)


@pytest.mark.asyncio
async def test_empty_state_explains_itself_and_survives_pane_keys(tmp_path: Path) -> None:
    app = ScopeApp(tmp_path / "nothing-here")
    async with app.run_test(size=(100, 30)) as pilot:
        assert "no SILICA artifacts here" in screen_text(app)
        for key in ("1", "2", "3", "4", "5", "m", "f", "s", "n", "i", "x", "slash"):
            await pilot.press(key)
            await pilot.pause()
        assert app.is_running


@pytest.mark.asyncio
async def test_published_checkout_degrades_without_claiming_a_sweep(
    published_artifacts: Path,
) -> None:
    app = ScopeApp(published_artifacts)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, lambda: "no per-tool metrics" in screen_text(app))
        text = screen_text(app)
        assert "no sweep artifacts here" in text
        assert "no per-tool metrics" in text
        await pilot.press("3")
        await pilot.pause()
        assert "nothing to browse" in screen_text(app)


@pytest.mark.asyncio
async def test_corrupt_artifacts_report_rather_than_crash(corrupt_artifacts: Path) -> None:
    app = ScopeApp(corrupt_artifacts)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, lambda: "artifact problem" in screen_text(app))
        assert "artifact problem" in screen_text(app)
        await pilot.press("3")
        await settle(pilot, lambda: "corrupt zstd stream" in screen_text(app))
        assert "corrupt zstd stream" in screen_text(app)
        assert app.is_running


@pytest.mark.asyncio
async def test_narrow_layout_switches_and_switches_back(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        assert not app.screen.has_class("narrow")
        assert not app.query_one(SpaceMap).compact
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert app.screen.has_class("narrow")
        assert app.query_one(SpaceMap).compact
        await pilot.resize_terminal(140, 40)
        await pilot.pause()
        assert not app.screen.has_class("narrow")
        assert not app.query_one(SpaceMap).compact


@pytest.mark.asyncio
async def test_tab_activation_focuses_the_pane(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert isinstance(app.focused, SpaceMap)
        await pilot.press("4")
        await pilot.pause()
        assert isinstance(app.focused, ListView)
        await pilot.press("5")
        await pilot.pause()
        assert isinstance(app.focused, DataTable)


@pytest.mark.asyncio
async def test_enter_on_the_map_opens_that_shard_in_the_corpus(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "corpus"
        assert app.shard_id == 0


@pytest.mark.asyncio
async def test_reproducer_detail_follows_the_selection(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("4")
        await settle(pilot, lambda: "1 of 1" in screen_text(app))
        assert "1 of 1" in screen_text(app)
        assert isinstance(app.query_one("#repro-detail-body", Static), Static)


@pytest.mark.asyncio
async def test_reload_does_not_lose_the_screen(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("r")
        await settle(pilot, lambda: "encodings swept" in screen_text(app))
        assert "encodings swept" in screen_text(app)
        assert app.is_running


@pytest.mark.asyncio
async def test_paging_does_not_drop_unsorted_records(tmp_path: Path) -> None:
    # a shard's records are not word-ordered (the text tier is a reservoir
    # sample). page 2 must continue the stream, not filter by "word > last".
    import json

    import zstandard

    root = tmp_path / "artifacts"
    (root / "disagreements").mkdir(parents=True)
    words = [0x00FFFFFF - i * 3 for i in range(900)]
    payload = "".join(
        json.dumps(
            {
                "format_version": 1,
                "word": f"0x{w:08x}",
                "category": "VALIDITY",
                "oracle_valid": {"capstone": True, "llvm": False, "spec": False, "unicorn": False},
                "oracle_text": {"capstone": None, "llvm": None, "spec": None, "unicorn": None},
            },
            separators=(",", ":"),
        )
        + "\n"
        for w in words
    )
    (root / "disagreements" / "000.zst").write_bytes(
        zstandard.ZstdCompressor().compress(payload.encode())
    )
    app = ScopeApp(root)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("3")
        await settle(pilot, lambda: len(app.records) == 400)
        await pilot.press("n")
        await settle(pilot, lambda: len(app.records) == 800)
        await pilot.press("n")
        await settle(pilot, lambda: len(app.records) == 900)
        assert [r.word for r in app.records] == words
        assert app._exhausted


@pytest.mark.asyncio
async def test_moving_the_corpus_cursor_updates_the_detail(full_artifacts: Path) -> None:
    # exercises an @on handler that lives on a pane mixin: textual only
    # collects decorated handlers from a class body, so a mixin's handlers
    # are silently dead unless they are registered explicitly.
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("3")
        await settle(pilot, lambda: len(app.records) == 3)
        assert "0x00000000" in screen_text(app)
        await pilot.press("down")
        await pilot.press("down")
        await settle(pilot, lambda: "OPERAND" in screen_text(app))
        text = screen_text(app)
        assert "0x00000002" in text
        assert "adr x26, #-825080" in text


@pytest.mark.asyncio
async def test_moving_the_goal_cursor_updates_the_detail(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, full_artifacts.parent / "GOALS.yml")
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("5")
        await settle(pilot, lambda: "artifacts this goal produces" in screen_text(app))
        assert "g1_metrics" in screen_text(app)


@pytest.mark.asyncio
async def test_exact_channel_reads_the_bitmaps(full_artifacts: Path) -> None:
    # four tiny bitmaps covering one shard: spec all-zero, capstone all-ones,
    # so the exact channel must land on 1.0 for shard 0.
    shard_bytes = (1 << 24) // 8
    bitmaps = full_artifacts / "bitmaps"
    bitmaps.mkdir()
    (bitmaps / "spec.bin").write_bytes(b"\x00" * shard_bytes)
    (bitmaps / "capstone.bin").write_bytes(b"\xff" * shard_bytes)
    (bitmaps / "llvm.bin").write_bytes(b"\x00" * shard_bytes)
    (bitmaps / "unicorn.bin").write_bytes(b"\x00" * shard_bytes)
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("x")
        await settle(pilot, lambda: bool(app.query_one(SpaceMap).exact))
        assert app.query_one(SpaceMap).exact[0] == 1.0
        assert app.query_one(SpaceMap).mode.key == "exact"


@pytest.mark.asyncio
async def test_exact_channel_says_so_when_there_are_no_bitmaps(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        await pilot.press("x")
        for _ in range(40):
            await pilot.pause()
        assert not app.query_one(SpaceMap).exact
        assert app.is_running


@pytest.mark.asyncio
async def test_reload_in_the_empty_state_does_not_crash(tmp_path: Path) -> None:
    # r is advertised in the footer and the empty screen tells you to point
    # it somewhere real, so r is the likeliest key to be pressed there.
    app = ScopeApp(tmp_path / "nothing-here")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert app.is_running
        assert "no SILICA artifacts here" in screen_text(app)


@pytest.mark.asyncio
async def test_reload_refreshes_every_pane_not_just_the_overview(
    full_artifacts: Path, tmp_path: Path
) -> None:
    import shutil

    live = tmp_path / "live" / "artifacts"
    live.mkdir(parents=True)
    shutil.copytree(full_artifacts / "reproducers", live / "reproducers")
    (live / "result_hash.txt").write_text("c" * 64)
    app = ScopeApp(live, tmp_path / "live" / "GOALS.yml")
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("5")
        await settle(pilot, lambda: "no goals file" in screen_text(app))
        # the tree grows real artifacts underneath a running reader
        shutil.copytree(full_artifacts / "sweep", live / "sweep")
        shutil.copy(full_artifacts.parent / "GOALS.yml", tmp_path / "live" / "GOALS.yml")
        await pilot.press("r")
        await settle(pilot, lambda: "no goals file" not in screen_text(app))
        assert "Parse the ARM ISA XML" in screen_text(app)
        await pilot.press("2")
        await settle(pilot, lambda: "no shard record on disk" not in screen_text(app))
        text = screen_text(app)
        assert "shard 000" in text
        assert "no shard record on disk" not in text


@pytest.mark.asyncio
async def test_unreadable_corpus_directory_does_not_stop_startup(
    full_artifacts: Path,
) -> None:
    import os

    os.chmod(full_artifacts / "disagreements", 0o000)
    try:
        app = ScopeApp(full_artifacts, None)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.press("3")
            await settle(pilot, lambda: "nothing to browse" in screen_text(app))
            assert app.is_running
    finally:
        os.chmod(full_artifacts / "disagreements", 0o755)


@pytest.mark.asyncio
async def test_escape_cancels_a_running_scan(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("3")
        await settle(pilot, lambda: len(app.records) == 3)
        app._loading = True
        token = app._scan_token
        await pilot.press("escape")
        await pilot.pause()
        assert app._scan_token != token
        assert not app._loading
        assert "cancelled" in screen_text(app)


@pytest.mark.asyncio
async def test_shift_m_cycles_the_channel_backwards(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        smap = app.query_one(SpaceMap)
        start = smap.mode_index
        await pilot.press("m")
        await pilot.pause()
        assert smap.mode_index != start
        await pilot.press("M")
        await pilot.pause()
        assert smap.mode_index == start


@pytest.mark.asyncio
async def test_every_key_the_help_screen_documents_is_bound(full_artifacts: Path) -> None:
    # the help screen used to promise `escape` cancels a scan and `M` cycles
    # backwards; neither had a handler.
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("question_mark")
        await settle(pilot, lambda: "SILICA scope" in screen_text(app))
        text = screen_text(app)
    documented = {"m", "M", "x", "s", "f", "n", "i", "r", "escape", "enter"}
    handlers = {name[4:] for name in dir(app) if name.startswith("key_")}
    actions = {b.key for b in app.BINDINGS if hasattr(b, "key")}
    bound = handlers | {k for a in actions for k in a.split(",")}
    bound |= {"escape", "enter"}  # bound on modals and on the map widget
    missing = {k for k in documented if k not in bound and f"map: {k}" in text}
    assert not missing, f"help documents unbound keys: {missing}"


@pytest.mark.asyncio
async def test_lookup_miss_does_not_claim_text_agreement(full_artifacts: Path) -> None:
    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("slash")
        await pilot.pause()
        for ch in "0x00000005":
            await pilot.press(ch)
        await pilot.press("enter")
        await settle(pilot, lambda: "not in the corpus" in flat_text(app))
        text = screen_text(app)
        assert "all four agreed on validity (exhaustive)." in text
        assert "text was not sampled for this word." in text


@pytest.mark.asyncio
async def test_short_terminal_can_still_reach_every_shard(full_artifacts: Path) -> None:
    # regression: the 16-row grid used to be clipped with no scrollbar, so on
    # a short terminal rows c-f of the shard space were unreachable.
    from textual.containers import VerticalScroll

    app = ScopeApp(full_artifacts, None)
    async with app.run_test(size=(60, 18)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        container = app.query_one("#map-left", VerticalScroll)
        assert container.size.height < 17  # the grid genuinely does not fit
        await pilot.press("end")
        for _ in range(10):
            await pilot.pause()
        assert app.query_one(SpaceMap).cursor == 255
        cursor_row = 1 + 255 // 16
        top = container.scroll_offset.y
        assert top <= cursor_row < top + container.size.height
        await pilot.press("home")
        for _ in range(10):
            await pilot.pause()
        assert container.scroll_offset.y == 0
