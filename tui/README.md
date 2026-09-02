# silica-scope

Interactive terminal reader for a finished [SILICA](../README.md) sweep.

SILICA decodes all 2³² AArch64 encodings through Capstone, LLVM, Unicorn and
ARM's own XML spec and writes the result to `artifacts/`. `silica-scope` reads
those published artifacts and lets you walk them: per-tool agreement, a heat
map of the 256-shard encoding space, the disagreement corpus, and the
filing-ready reproducers.

It never runs a sweep. Pure Python, no native dependencies, nothing from
`crates/` — the sweep engine stays a source-clone + micromamba workflow
because it links Capstone/LLVM/Unicorn at build time.

## Install

```bash
pipx install ./tui          # from a SILICA checkout
pipx install silica-scope   # once published
```

## Run

```bash
silica-scope                          # finds the nearest artifacts/ from $PWD
silica-scope /path/to/silica/artifacts
SILICA_ARTIFACTS=/path/to/artifacts silica-scope
silica-scope --report                 # static summary, no TUI
```

## Panes

| key | pane | what's in it |
|---|---|---|
| `1` | overview | headline counts, per-tool agreement, taxonomy, provenance, what's on disk |
| `2` | map | the 2³² space as a 16×16 shard grid, four colour channels |
| `3` | corpus | stream `disagreements/*.zst`, filter by category, inspect any word |
| `4` | reproducers | the filing-ready writeups, parsed and cross-checked |

`?` for the full keymap, `/` to look up any 32-bit encoding, `r` to reload
from disk, `q` to quit.

The map's row is a shard id's high nibble and its column the low nibble, so
shard `0xab` sits at row `a`, column `b`, covering `0xab000000..0xabffffff`.
Press `m` to cycle the colour channel; `x` computes exact
`popcount(tool XOR spec)` per shard straight off `bitmaps/` when those are
present (it reads 4 × 512 MiB, so it's opt-in).

## Partial artifacts

A published SILICA checkout ships only `artifacts/reproducers/` and
`artifacts/result_hash.txt` — the bitmaps, shard records and corpus are
multi-gigabyte and regenerated locally. `silica-scope` treats that as a normal
state, not an error: panes that have no data say what's missing and where it
looked, and the overview never claims a sweep it can't see evidence of. A file
that exists but is malformed *is* reported, per pane and in a problems banner.

Shard records are treated as evidence, not hints. For a record under
`sweep/shards/` to appear on the map it must match the documented SILICA
format: its zero-padded filename and embedded ID agree, its range is the exact
`2**24`-word interval for that ID, all four oracle counts are present and in
bounds, and its crash state and content hash are well formed. A malformed
record is listed as an artifact problem and does not contribute to the
"complete shards" total.

Likewise, the reader only says it has full-sweep evidence from shard records
when the accepted records contain every distinct ID from 0 through 255. A
directory merely containing 256 JSON files is not enough. Published metrics
or G1 metrics remain independent full-sweep evidence because those are the
finished pipeline's summary artifacts.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ./tui pytest pytest-asyncio
.venv/bin/python -m pytest tui/tests
```

Apache-2.0.

`tui/tools/shot.py` and `tui/tools/drive.py` render the app headlessly to PNG
(needs `cairosvg`) — how the layout was checked against the real artifacts
rather than by reading the code.
