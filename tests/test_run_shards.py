from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "run_shards.py"
    spec = importlib.util.spec_from_file_location("run_shards", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_shards = _load_script()


def _config(tmp_path: Path, jobs: int = 2):
    return run_shards.RunConfig(
        sweep_bin=tmp_path / "silica-sweep",
        decode_table=tmp_path / "decode-table.bin",
        out=tmp_path / "out",
        jobs=jobs,
    )


@pytest.mark.parametrize(
    ("requested", "shard_total", "cpus", "expected"),
    [
        (0, 256, 32, 32),
        (0, 4, 32, 4),
        (2, 20, 32, 2),
        (99, 5, 2, 5),
    ],
)
def test_resolve_jobs(requested: int, shard_total: int, cpus: int | None, expected: int) -> None:
    assert run_shards.resolve_jobs(requested, shard_total, cpus) == expected


def test_resolve_jobs_falls_back_to_one_when_cpu_count_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(run_shards.os, "cpu_count", lambda: None)
    assert run_shards.resolve_jobs(0, 3) == 1


@pytest.mark.parametrize(
    ("requested", "shard_total", "message"),
    [(-1, 1, "nonnegative"), (0, 0, "at least one")],
)
def test_resolve_jobs_rejects_invalid_input(
    requested: int, shard_total: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        run_shards.resolve_jobs(requested, shard_total, 4)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (-1, 1, "start shard"),
        (256, 256, "start shard"),
        (0, 0, "end shard"),
        (0, 257, "end shard"),
        (4, 4, "less than"),
        (5, 4, "less than"),
    ],
)
def test_shard_ids_reject_invalid_ranges(start: int, end: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        run_shards.shard_ids(start, end)


def test_parallel_results_keep_requested_order(tmp_path: Path) -> None:
    config = _config(tmp_path, jobs=4)

    def runner(config, shard_id: int):
        del config
        time.sleep((7 - shard_id) * 0.001)
        return run_shards.ShardResult(shard_id, 0, stderr=f"done {shard_id}")

    results = run_shards.run_parallel(config, (3, 4, 5, 6), runner)
    assert [result.shard_id for result in results] == [3, 4, 5, 6]
    assert [result.stderr for result in results] == ["done 3", "done 4", "done 5", "done 6"]


def test_parallel_runner_obeys_concurrency_cap(tmp_path: Path) -> None:
    config = _config(tmp_path, jobs=3)
    lock = threading.Lock()
    active = 0
    peak = 0

    def runner(config, shard_id: int):
        nonlocal active, peak
        del config
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return run_shards.ShardResult(shard_id, 0)

    results = run_shards.run_parallel(config, tuple(range(12)), runner)
    assert all(result.passed for result in results)
    assert peak == 3
    assert active == 0


def test_parallel_runner_collects_failures_without_skipping_later_shards(tmp_path: Path) -> None:
    config = _config(tmp_path, jobs=4)
    called: list[int] = []
    lock = threading.Lock()

    def runner(config, shard_id: int):
        del config
        with lock:
            called.append(shard_id)
        code = 9 if shard_id == 2 else 0
        return run_shards.ShardResult(shard_id, code, stderr="broken" if code else "")

    results = run_shards.run_parallel(config, tuple(range(6)), runner)
    assert sorted(called) == list(range(6))
    assert [result.returncode for result in results] == [0, 0, 9, 0, 0, 0]


def test_parallel_runner_turns_exception_into_shard_failure(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def runner(config, shard_id: int):
        del config
        if shard_id == 1:
            raise RuntimeError("boom")
        return run_shards.ShardResult(shard_id, 0)

    results = run_shards.run_parallel(config, (0, 1, 2), runner)
    assert results[1].returncode == 1
    assert results[1].stderr == "launcher error: boom"


def test_parallel_runner_rejects_mismatched_result_id(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def runner(config, shard_id: int):
        del config
        return run_shards.ShardResult(shard_id + 1, 0)

    result = run_shards.run_parallel(config, (4,), runner)[0]
    assert result.returncode == 1
    assert "submitted 4, received 5" in result.stderr


def test_run_one_captures_subprocess_output(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    seen: list[object] = []

    class Completed:
        returncode = 3
        stdout = "out"
        stderr = "err"

    def fake_run(command, **kwargs):
        seen.extend([command, kwargs])
        return Completed()

    monkeypatch.setattr(run_shards.subprocess, "run", fake_run)
    result = run_shards.run_one(config, 8)
    assert result == run_shards.ShardResult(8, 3, "out", "err")
    assert seen[0] == run_shards.command_for(config, 8)
    assert seen[1] == {"capture_output": True, "text": True, "check": False}


def test_run_one_reports_spawn_failure(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)

    def fail_run(*args, **kwargs):
        del args, kwargs
        raise OSError("missing")

    monkeypatch.setattr(run_shards.subprocess, "run", fail_run)
    result = run_shards.run_one(config, 9)
    assert result.returncode == 127
    assert "could not start shard: missing" in result.stderr


def test_print_results_is_ordered_and_concise(capsys) -> None:
    results = [
        run_shards.ShardResult(1, 0, stderr="first\nalready complete"),
        run_shards.ShardResult(2, 5, stdout="fallback detail"),
        run_shards.ShardResult(3, 7, stderr="line one\nlast error"),
    ]
    run_shards.print_results(results)
    captured = capsys.readouterr()
    assert captured.out == "[PASS] shard 001 (already complete)\n"
    assert captured.err.splitlines() == [
        "[FAIL] shard 002 exit 5: fallback detail",
        "[FAIL] shard 003 exit 7: last error",
    ]


def test_main_requires_existing_inputs(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        run_shards.main(
            [
                "--sweep-bin",
                str(tmp_path / "missing"),
                "--decode-table",
                str(tmp_path / "table"),
                "--out",
                str(tmp_path / "out"),
            ]
        )
    assert exc.value.code == 2
    assert "sweep binary is not a file" in capsys.readouterr().err


def test_main_returns_zero_after_every_shard_passes(tmp_path: Path, monkeypatch, capsys) -> None:
    sweep_bin = tmp_path / "silica-sweep"
    decode_table = tmp_path / "decode-table.bin"
    sweep_bin.write_bytes(b"binary")
    decode_table.write_bytes(b"table")
    seen: list[tuple[object, tuple[int, ...]]] = []

    def fake_parallel(config, shards, runner=run_shards.run_one):
        del runner
        seen.append((config, tuple(shards)))
        return [run_shards.ShardResult(shard_id, 0) for shard_id in shards]

    monkeypatch.setattr(run_shards, "run_parallel", fake_parallel)
    status = run_shards.main(
        [
            "--sweep-bin",
            str(sweep_bin),
            "--decode-table",
            str(decode_table),
            "--out",
            str(tmp_path / "out"),
            "--jobs",
            "3",
            "--start-shard",
            "4",
            "--end-shard",
            "7",
        ]
    )
    assert status == 0
    assert seen[0][0].jobs == 3
    assert seen[0][1] == (4, 5, 6)
    assert capsys.readouterr().out.endswith("run_shards: 3 shards completed\n")


def test_main_returns_one_when_any_shard_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    sweep_bin = tmp_path / "silica-sweep"
    decode_table = tmp_path / "decode-table.bin"
    sweep_bin.write_bytes(b"binary")
    decode_table.write_bytes(b"table")

    def fake_parallel(config, shards, runner=run_shards.run_one):
        del config, runner
        return [
            run_shards.ShardResult(shard_id, 4 if shard_id == 1 else 0, stderr="bad")
            for shard_id in shards
        ]

    monkeypatch.setattr(run_shards, "run_parallel", fake_parallel)
    status = run_shards.main(
        [
            "--sweep-bin",
            str(sweep_bin),
            "--decode-table",
            str(decode_table),
            "--out",
            str(tmp_path / "out"),
            "--end-shard",
            "3",
        ]
    )
    assert status == 1
    captured = capsys.readouterr()
    assert "[FAIL] shard 001 exit 4: bad" in captured.err
    assert "run_shards: 1 of 3 shards failed" in captured.err


def test_make_all_uses_parallel_launcher() -> None:
    makefile = (Path(__file__).parents[1] / "Makefile").read_text()
    all_recipe = makefile.split("\nall:\n", maxsplit=1)[1]
    assert "python scripts/run_shards.py" in all_recipe
    assert "--jobs $(SWEEP_JOBS)" in all_recipe
    assert "for shard in" not in all_recipe
