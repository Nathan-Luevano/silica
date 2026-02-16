# Artifact formats

Contracts for everything under `artifacts/` (gitignored, regenerated). A
verifier checks both producer and consumer agree with what's written here.

## decode-table.bin

Produced by the Python spec compiler (`pysilica/spec/`), consumed nowhere
outside verification in P1; the Rust sweep gets its own compiled form later.
Format not yet fixed — P1 fixes it before `mra.py` writes the first byte.

## g1_metrics.json

Produced by the P1 spec-compiler run. Flat JSON object, required keys:

- `spec_release` — string, must equal `manifests/spec.yml: spec.release`
- `tiling_files_checked` — int, count of instruction XML files checked for
  the 32-bit box-tiling invariant (§5.2)
- `tiling_files_passed` — int, must equal `tiling_files_checked` for G1 to pass
- `allocated` — int, count of 32-bit words classified ALLOCATED
- `unallocated` — int, count classified UNALLOCATED (`allocated + unallocated
  == 2**32`)
- `ret_test_word` — string, hex of the RET encoding used as the parser's first
  unit test, e.g. `"0xd65f03c0"`
- `ret_test_passed` — bool

## bitmaps/<oracle>.bin

One bit per 32-bit encoding, bit index == encoding value (word `N`'s bit lives
at byte `N // 8`, bit `N % 8`, LSB-first). 1 = oracle decoded a valid
instruction, 0 = rejected. Exactly `2**32` bits = 512 MiB per file,
memory-mappable. Popcount + zero-count must sum to exactly `2**32`
(verified by G2).

## disagreements/*.zst

One record per line (newline-delimited JSON, zstd-compressed). Each record
carries a `format_version` field; consumers must reject records whose version
they don't recognize rather than guess. Fields fixed in P4 alongside the
triage code, before the first record is written.

## sweep/shards/<NNN>.json

Shard completion record, one per shard, `NNN` zero-padded 0..255. Shard `i`
covers encodings `[i * 2**24, (i + 1) * 2**24)` — 256 shards tile
`[0, 2**32)` exactly, no gap, no overlap. Fields fixed in P3 alongside the
sharding code.
