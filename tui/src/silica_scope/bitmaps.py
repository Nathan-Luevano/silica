from __future__ import annotations

import time
from pathlib import Path

from .model import SHARD_COUNT, TOOLS

SHARD_BYTES = (1 << 24) // 8


def exact_disagreement_fractions(bitmaps_dir: Path) -> dict[int, float]:
    # popcount(tool XOR spec) per shard, straight off the bitmaps. reads
    # 4 x 512 MiB, which is why it is opt-in rather than done at startup.
    spec_path = bitmaps_dir / "spec.bin"
    if not spec_path.is_file():
        return {}
    tools = [t for t in TOOLS if (bitmaps_dir / f"{t}.bin").is_file()]
    if not tools:
        return {}
    out: dict[int, float] = {}
    handles = {t: (bitmaps_dir / f"{t}.bin").open("rb") for t in tools}
    try:
        with spec_path.open("rb") as spec_fh:
            for shard_id in range(SHARD_COUNT):
                offset = shard_id * SHARD_BYTES
                spec_fh.seek(offset)
                spec_bytes = spec_fh.read(SHARD_BYTES)
                if len(spec_bytes) < SHARD_BYTES:
                    break
                spec_int = int.from_bytes(spec_bytes, "little")
                worst = 0
                for tool in tools:
                    fh = handles[tool]
                    fh.seek(offset)
                    chunk = fh.read(SHARD_BYTES)
                    if len(chunk) < SHARD_BYTES:
                        continue
                    diff = (int.from_bytes(chunk, "little") ^ spec_int).bit_count()
                    worst = max(worst, diff)
                out[shard_id] = worst / (SHARD_BYTES * 8)
                # int.from_bytes and int.bit_count hold the GIL; without this
                # the UI thread waits seconds for its next keystroke.
                time.sleep(0)
    finally:
        for fh in handles.values():
            fh.close()
    return out
