from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

SHARD_COUNT = 256


@dataclass(frozen=True)
class RunConfig:
    sweep_bin: Path
    decode_table: Path
    out: Path
    jobs: int


@dataclass(frozen=True)
class ShardResult:
    shard_id: int
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def passed(self) -> bool:
        return self.returncode == 0


Runner = Callable[[RunConfig, int], ShardResult]


def resolve_jobs(requested: int, shard_total: int, cpu_count: int | None = None) -> int:
    if requested < 0:
        raise ValueError("jobs must be nonnegative")
    if shard_total < 1:
        raise ValueError("at least one shard is required")
    available = cpu_count if cpu_count is not None else os.cpu_count()
    chosen = requested or available or 1
    return min(chosen, shard_total)


def shard_ids(start: int, end: int) -> tuple[int, ...]:
    if not 0 <= start < SHARD_COUNT:
        raise ValueError(f"start shard must be in 0..{SHARD_COUNT - 1}")
    if not 1 <= end <= SHARD_COUNT:
        raise ValueError(f"end shard must be in 1..{SHARD_COUNT}")
    if start >= end:
        raise ValueError("start shard must be less than end shard")
    return tuple(range(start, end))


def command_for(config: RunConfig, shard_id: int) -> list[str]:
    return [
        str(config.sweep_bin),
        "run",
        "--shard",
        str(shard_id),
        "--spec-decode-table",
        str(config.decode_table),
        "--out",
        str(config.out),
    ]


def run_one(config: RunConfig, shard_id: int) -> ShardResult:
    try:
        completed = subprocess.run(
            command_for(config, shard_id),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return ShardResult(shard_id, 127, stderr=f"could not start shard: {exc}")
    return ShardResult(
        shard_id,
        completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_parallel(config: RunConfig, shards: Sequence[int], runner: Runner = run_one) -> list[ShardResult]:
    workers = resolve_jobs(config.jobs, len(shards))
    indexed: dict[int, ShardResult] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="silica-shard") as pool:
        pending: dict[Future[ShardResult], int] = {
            pool.submit(runner, config, shard_id): shard_id for shard_id in shards
        }
        for future in as_completed(pending):
            shard_id = pending[future]
            try:
                result = future.result()
            # a failed future is one failed shard, not permission to lose the
            # remaining completed results or hide which shard raised.
            except Exception as exc:  # noqa: BLE001
                result = ShardResult(shard_id, 1, stderr=f"launcher error: {exc}")
            if result.shard_id != shard_id:
                result = ShardResult(
                    shard_id,
                    1,
                    stderr=(
                        f"launcher result mismatch: submitted {shard_id}, "
                        f"received {result.shard_id}"
                    ),
                )
            indexed[shard_id] = result
    return [indexed[shard_id] for shard_id in shards]


def _last_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def print_results(results: Sequence[ShardResult]) -> None:
    for result in results:
        if result.passed:
            detail = _last_nonempty_line(result.stderr)
            suffix = f" ({detail})" if detail else ""
            print(f"[PASS] shard {result.shard_id:03d}{suffix}")
            continue
        detail = _last_nonempty_line(result.stderr) or _last_nonempty_line(result.stdout)
        suffix = f": {detail}" if detail else ""
        print(
            f"[FAIL] shard {result.shard_id:03d} exit {result.returncode}{suffix}",
            file=sys.stderr,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep-bin", type=Path, required=True)
    parser.add_argument("--decode-table", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="concurrent shards; 0 uses all available logical CPUs",
    )
    parser.add_argument("--start-shard", type=int, default=0)
    parser.add_argument("--end-shard", type=int, default=SHARD_COUNT)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        shards = shard_ids(args.start_shard, args.end_shard)
        resolve_jobs(args.jobs, len(shards))
    except ValueError as exc:
        parser.error(str(exc))
    if not args.sweep_bin.is_file():
        parser.error(f"sweep binary is not a file: {args.sweep_bin}")
    if not args.decode_table.is_file():
        parser.error(f"decode table is not a file: {args.decode_table}")

    config = RunConfig(args.sweep_bin, args.decode_table, args.out, args.jobs)
    results = run_parallel(config, shards)
    print_results(results)
    failures = sum(not result.passed for result in results)
    if failures:
        print(f"run_shards: {failures} of {len(results)} shards failed", file=sys.stderr)
        return 1
    print(f"run_shards: {len(results)} shards completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
