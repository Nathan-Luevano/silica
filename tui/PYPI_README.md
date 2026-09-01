# silica-scope

Interactive terminal reader for a finished SILICA AArch64 decode sweep.

[SILICA](https://github.com/nluevano/silica) decodes all 2³² AArch64
encodings through Capstone, LLVM, Unicorn and ARM's own XML spec and writes
the result to `artifacts/`. `silica-scope` reads those published artifacts
and lets you walk them: per-tool agreement, a heat map of the 256-shard
encoding space, the disagreement corpus, and the filing-ready reproducers.

It never runs a sweep. Pure Python, no native dependencies — reading a
finished sweep is all it does.

## Install

```bash
pipx install silica-scope
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

## Source

Source, issues and the full SILICA sweep engine live at
[github.com/nluevano/silica](https://github.com/nluevano/silica) — see
`tui/README.md` there for building from a checkout.

Apache-2.0.
