# Artifact formats

Contracts for everything under `artifacts/` (gitignored, regenerated). A
verifier checks both producer and consumer agree with what's written here.

## decode-table.bin

Produced by the Python spec compiler (`pysilica/spec/mra.py` +
`pysilica/spec/tables.py`), consumed nowhere outside verification and
`pysilica` tests in P1; the Rust sweep (P2+) gets its own compiled form
later and is free to ignore this file entirely.

The table is a binary-decision tree over the 32 encoding bits, one leaf per
distinguishable region of the 2^32 word space, built from every
`type="instruction"` `<regdiagram>` in the spec XML. Nodes are stored as a
flat array; a node's children are referenced by index into that same array
(indices always point earlier in the array — the tree is built bottom-up and
written in construction order, so no forward references exist). All
integers little-endian.

```
magic            4 bytes   b"SIL1"
spec_release_len u32
spec_release     UTF-8, spec_release_len bytes   (e.g. "ISA_A64_xml_A_profile-2026-06_mc")
form_count       u32
forms[form_count]:
  psname_len      u32
  psname          UTF-8 bytes                     (the decode-tree key, DESIGN-FINAL.md §5.4)
  encoding_names_len  u32
  encoding_names  UTF-8 bytes, "|"-joined <encoding name> values sharing this psname
  mnemonic_len    u32
  mnemonic        UTF-8 bytes
  gating_len      u32
  gating          UTF-8 bytes, ","-joined FEAT_* names from <arch_variants> (empty = ungated)
node_count       u32
nodes[node_count]:
  kind            u8        0 = internal, 1 = leaf ALLOCATED, 2 = leaf UNALLOCATED
  bit             u8        bit position 31..0 this node splits on; 0 for leaf kinds
  a               u32       kind 0: index of the child taken when `bit` is 0
                             kind 1: index into forms[] for the matched form
                             kind 2: unused, written as 0
  b               u32       kind 0: index of the child taken when `bit` is 1
                             kind 1: count of forms whose fixed bits are
                                     indistinguishable from the matched one at
                                     this leaf (>1 means the regdiagram alone
                                     cannot name the instruction uniquely —
                                     see "known limitations" below; the word
                                     is still definitely ALLOCATED)
                             kind 2: unused, written as 0
root_index       u32       index into nodes[] of the tree root
```

To classify a 32-bit word: start at `root_index`, and at each internal node
test `(word >> bit) & 1`, following child `a` for 0 or child `b` for 1, until
a leaf is reached. Leaves only occur once every bit position 31..0 has been
consulted. Because of node-array sharing (memoized on the set of
still-possibly-matching forms plus current bit), the array is far smaller
than 2^32 entries — building it against the real 2026-06 spec produces on the
order of 10^4-10^5 nodes, not 10^9.

**Known limitations** (see `g1_metrics.json`'s `decode_time_undefined_forms`
and `ambiguous_leaf_groups`):
- The tree is built purely from fixed regdiagram bits. Some instructions
  (e.g. the AArch64 hint space: `PACIA1716`/`AUTIA1716`/`NOP`/... under
  `A64.control.hints.HINT_HM_hints`) share one regdiagram and are actually
  disambiguated by a decode-time dispatch on field *values*, described only
  in prose/pseudocode outside the regdiagram. Such words are still correctly
  classified ALLOCATED; only the attached form name may be one of several
  valid names for that word (`b` count > 1 on the leaf records this).
- Decode-time `UNDEFINED`/`UNPREDICTABLE`/`UnallocatedEncoding` conditions
  keyed on field values, if present in a `<pstext section="Decode">` or
  `section="Postdecode"` block, are not evaluated — the compiler only greps
  those two sections for those literal ASL calls and counts files where they
  appear (`decode_time_undefined_forms`), it does not act on them. Measured
  against the real 2026-06 base-A64 corpus this count is 0 (see
  `g1_metrics.json`), meaning no over-acceptance from this source was
  detected by that heuristic — but the heuristic is a keyword grep, not an
  ASL interpreter, and would miss an equivalent condition phrased without
  those exact calls.

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

Extra keys, not required by `verify_g1_spec_oracle` but written for
transparency per DESIGN-FINAL.md §5.3:

- `decode_time_undefined_forms` — int, count of `type="instruction"` files
  whose `<pstext section="Decode">` or `section="Postdecode"` text contains
  `UNDEFINED`, `UNPREDICTABLE`, `UnallocatedEncoding`, `EndOfInstruction`, or
  `ReservedEncoding` — a keyword-grep proxy for decode-time field-value
  conditions the regdiagram-only tree does not evaluate. Not a full ASL
  interpreter; see docs/formats.md's decode-table.bin section for caveats.
- `ambiguous_leaf_groups` — int, count of distinct regdiagram fixed-bit
  signatures shared by more than one named form (leaf `b` count > 1 in
  decode-table.bin). Affects only which name is attached to a word, never
  the allocated/unallocated boundary.
- `instruction_files` / `alias_files` — int counts of `type="instruction"`
  and `type="alias"` instructionsection files found under `xml_dir`.

## bitmaps/<oracle>.bin

One bit per 32-bit encoding, bit index == encoding value (word `N`'s bit lives
at byte `N // 8`, bit `N % 8`, LSB-first). 1 = oracle decoded a valid
instruction, 0 = rejected. Exactly `2**32` bits = 512 MiB per file,
memory-mappable. Popcount + zero-count must sum to exactly `2**32`
(verified by G2).

## disagreements/*.zst

One record per line (newline-delimited JSON, zstd-compressed). Each record
carries a `format_version` field; consumers must reject records whose version
they don't recognize rather than guess. One file per shard that has any
disagreements (`disagreements/<NNN>.zst`); a shard with zero disagreements
produces no file, not an empty one.

Record schema (`format_version: 1`):

```json
{
  "format_version": 1,
  "word": "0xd65f03c0",
  "category": "VALIDITY",
  "oracle_valid": {"capstone": true, "llvm": true, "spec": true, "unicorn": false},
  "oracle_text": {"capstone": "ret", "llvm": "ret", "spec": "RET", "unicorn": null}
}
```

- `category` — one of the DESIGN-FINAL.md §7 taxonomy values: `VALIDITY`,
  `MNEMONIC`, `OPERAND`, `ALIAS`, `FORMATTING`, `NORMALIZATION_UNCERTAIN`,
  `CRASH`. `EQUIVALENT` (from `normalize.classify_disagreement`) never
  appears here — an equivalent pair isn't a disagreement, it's resolved,
  and doesn't get a record at all.
- `oracle_valid` — always all four exhaustive-tier oracles (`capstone`,
  `llvm`, `spec`, `unicorn`), computed directly from `bitmaps/<oracle>.bin`
  for that word — this is what makes `VALIDITY`-category records checkable
  against the bitmaps independent of anything else in the pipeline.
- `oracle_text` — raw disassembly text per oracle where available, `null`
  where the oracle rejected the word (`oracle_valid[x] == false`) or where
  text wasn't captured for that category (a pure `VALIDITY` disagreement
  where all four already agree on invalid isn't a candidate at all — only
  emitted when at least one oracle disagrees on validity, or all agree
  valid but text differs post-normalization).
- A `CRASH`-category record has `oracle_valid[oracle] == false` for the
  crashing oracle by construction (a crash is recorded as invalid in the
  bitmap, matching `sweep/shards/<NNN>.json`'s `crash_count` for that
  shard) and `oracle_text[oracle] == null`; it's the `crash_count` in the
  shard record that's authoritative for "how many crashes", these records
  are for classifying *which* words and getting them into the same corpus
  as everything else.

**Exhaustiveness scope, stated plainly per DESIGN-FINAL.md §14 risk #2's
fallback:** `VALIDITY`-category coverage is exhaustive — computable
directly from the four already-swept bitmaps for every one of 2^32 words,
no sampling. Text-level categories (`MNEMONIC`/`OPERAND`/`ALIAS`/
`FORMATTING`/`NORMALIZATION_UNCERTAIN`) require actually disassembling a
word through all four oracles, which the validity-only sweep did not
capture (design.md §6: "do not store 4.3 billion decode strings"). If the
implementation cannot exhaustively re-disassemble every all-four-valid
word in reasonable time, it MUST state the actual sampling method and
denominator used for text-tier categories in `g4_metrics.json` (see
below) and in the worklog — never silently present a sample as exhaustive.

## g4_metrics.json

Produced alongside the corpus. Flat JSON object:

- `format_version` — int
- `shards_with_disagreements` — int, count of `disagreements/<NNN>.zst` files
- `total_disagreements` — int, total record count across all shard files
- `category_counts` — object, `{category: count}` for every taxonomy value
  that appears at least once
- `validity_tier_exhaustive` — bool, must be `true`
- `validity_disagreements` — int, count of `VALIDITY`-category records
- `text_tier_method` — string, one of `"exhaustive"` or `"sampled"`
- `text_tier_sample_size` / `text_tier_population` — int, required and
  meaningful when `text_tier_method == "sampled"`

## sweep/shards/<NNN>.json

Shard completion record, one per shard, `NNN` zero-padded 0..255 (e.g.
`sweep/shards/000.json` .. `sweep/shards/255.json`). Shard `i` covers
encodings `[i * 2**24, (i + 1) * 2**24)` — 256 shards tile `[0, 2**32)`
exactly, no gap, no overlap. Flat JSON object:

```
{
  "shard_id": 0,
  "start": 0,
  "end": 16777216,
  "oracles": ["capstone", "llvm", "spec", "unicorn"],
  "valid_counts": {"capstone": 0, "llvm": 0, "spec": 0, "unicorn": 0},
  "crash_count": 0,
  "untriaged_crash_count": 0,
  "content_hash": "<sha256 hex>",
  "duration_ms": 0,
  "status": "complete"
}
```

- `oracles` — always exactly `["capstone", "llvm", "spec", "unicorn"]`, this
  fixed alphabetical order, matching design.md §6's exhaustive tier (objdump
  excluded per the binutils-lacks-aarch64-target finding recorded in
  WORKLOG.md; ghidra is sampled tier, never shard-swept this way).
- `valid_counts[oracle]` — count of the `2**24` words in this shard's range
  that oracle classified valid. Must equal the popcount of that oracle's
  bitmap over exactly this shard's byte range (`bitmaps/<oracle>.bin` bytes
  `[start // 8, end // 8)`).
- `crash_count` — words in this shard where invoking that oracle crashed
  (segfault, panic, timeout) rather than returning valid/invalid.
  `untriaged_crash_count` starts equal to `crash_count` and is decremented as
  P4's triage classifies each crash; a shard may not be `"status":
  "complete"` while `untriaged_crash_count > 0` (design.md §9's "no shard
  marked complete with an untriaged crash count").
- `content_hash` — sha256 hex of the four oracles' bitmap byte ranges for
  `[start // 8, end // 8)`, concatenated in the `oracles` field's order
  (capstone, then llvm, then spec, then unicorn). This is what "re-running a
  shard reproduces its recorded hash" (design.md §9) checks: re-run the
  shard, recompute this same hash from the fresh output, compare.
- `status` — `"complete"` or `"crashed"` (shard-level abort, distinct from a
  per-word crash counted in `crash_count`).

## sweep CLI contract

`silica-sweep run --shard <N> --spec-decode-table <path> --out <dir>` runs
shard `N` (0..255) against all four exhaustive-tier oracles and:
- writes/updates `<dir>/bitmaps/<oracle>.bin` for each oracle — the full
  `2**32`-bit memory-mapped file (created zero-filled on first touch if
  absent), writing only this shard's own byte range. Safe to run multiple
  shards concurrently against the same files since ranges are disjoint.
- writes `<dir>/sweep/shards/<NNN>.json` per the schema above.

`silica-sweep verify-shard --shard <N> --spec-decode-table <path> --out
<scratch-dir>` re-runs shard `N` in isolation, writing small per-shard-only
bitmap slices (`2**24` bits = 2 MiB each, not the full file) under
`<scratch-dir>/bitmaps/<oracle>-<NNN>.bin` plus a shard record at
`<scratch-dir>/sweep/shards/<NNN>.json`, without touching any real
`artifacts/` output. This is what G2's verifier shells out to.

## artifacts/report/metrics.json

G5's published output: per-tool agreement against the spec oracle,
macro and micro, honest denominators, worst tool first (design.md §32,
P5). Flat JSON object:

- `format_version` — int, `1`
- `total_words` — int, must be `2**32`
- `spec_valid_count` — int, must equal the popcount of `bitmaps/spec.bin`
- `per_tool` — object keyed by `"capstone"`, `"llvm"`, `"unicorn"` (spec
  is the baseline, never compared to itself), each value:
  - `validity_disagreements_with_spec` — int, count of words where this
    tool's valid/invalid classification differs from spec's. Must equal
    `popcount(bitmaps/<tool>.bin XOR bitmaps/spec.bin)` computed directly
    from the bitmaps, not from the (sampled) disagreement corpus.
  - `validity_agreement_micro` — float, `(total_words -
    validity_disagreements_with_spec) / total_words`. The standard
    aggregate rate: every one of the 2**32 words counted once.
  - `macro_validity_agreement` — float, the *unweighted* mean of each of
    the 256 shards' own agreement rate (`(shard_size -
    shard_disagreements) / shard_size`). Differs from micro whenever
    disagreements cluster unevenly across shards (they do: see G4's
    finding that ~3 shards are almost entirely spec-only-valid) — a
    sparse shard and a dense shard count equally in macro, so a
    concentrated failure mode doesn't get diluted into invisibility by
    the vast majority of clean shards the way micro would.
  - `text_tier_disagreements_with_spec` — int, count of disagreement
    corpus records in a text-tier category
    (MNEMONIC/OPERAND/ALIAS/FORMATTING/NORMALIZATION_UNCERTAIN) where
    this tool's normalized text differs from spec's, counted by
    streaming `disagreements/*.zst` (never materializing the corpus —
    see the G4 OOM incident this project already hit once).
  - `text_tier_agreement_micro` — float, `(text_tier_population -
    text_tier_disagreements_with_spec) / text_tier_population`.
  - `text_tier_method`, `text_tier_sample_size`, `text_tier_population`
    — must equal `g4_metrics.json`'s same-named fields exactly (single
    source of truth for what was actually sampled vs exhaustive; no
    re-deriving a second, possibly-inconsistent claim here).
- `tool_ranking_worst_first` — array of the three tool names, sorted
  ascending by `validity_agreement_micro` (lowest agreement — i.e. worst
  — first). This is the "lead with the least flattering framing"
  requirement (design.md §32) made mechanically checkable: the report's
  own ordering must match a straight sort of its own numbers, not a
  cherry-picked one.
