.PHONY: check fmt lint test verify doctor hooks-test all

check:
	micromamba run -p ./.venv cargo fmt --check
	micromamba run -p ./.venv cargo clippy --workspace -- -D warnings
	micromamba run -p ./.venv cargo test --workspace
	micromamba run -p ./.venv ruff check .
	micromamba run -p ./.venv mypy --strict pysilica
	micromamba run -p ./.venv lint-imports
	micromamba run -p ./.venv pytest -q
	micromamba run -p ./.venv python scripts/no_docstrings.py
	! grep -rnE '^\s*(///|//!)' crates/
	micromamba run -p ./.venv python scripts/check_hooks.py
	micromamba run -p ./.venv python scripts/check_verifier_hashes.py
	micromamba run -p ./.venv python scripts/check_worklog_drift.py
	# leading - : shown, not gated, until G1-G7 are actually implemented.
	# `make verify` below is the real per-goal gate; v1 is done when both
	# this line and `make verify` are green (design.md §8 P6, §12.1).
	-micromamba run -p ./.venv python -m pysilica.cli verify

doctor:
	micromamba run -p ./.venv python -m pysilica.cli doctor

verify:
	micromamba run -p ./.venv python -m pysilica.cli verify

# full pipeline from a clean checkout, in order (G7). Not run as part of
# `make check` - the 256-shard sweep alone takes many hours (see G2/G4
# worklog entries); this documents the real one-command shape a human
# would actually invoke. Each step's inputs/outputs are the same ones
# every prior goal's verifier already checks against.
#
# the g4-tier1 -> g4-disasm handoff needs one conversion this recipe
# doesn't spell out: g4-tier1's reservoir sample lands in
# <scratch>/tier2_candidates.json (see g4.rs), and g4-disasm wants a
# plain word-list file - extracting one from the other is a few lines
# of jq/python, omitted here rather than guessed at and presented as
# real. the three numeric g4_run flags are this project's actual
# measured values (WORKLOG.md, 2026-08-21 f9a0f56) - a fresh run would
# read them off g4-tier1's own stderr summary instead of hardcoding.
all:
	micromamba run -p ./.venv cargo build --release -p silica-sweep
	micromamba run -p ./.venv python -m pysilica.cli compile-spec
	for shard in $$(seq 0 255); do \
		./target/release/silica-sweep run --shard $$shard \
			--spec-decode-table artifacts/decode-table.bin --out artifacts; \
	done
	./target/release/silica-sweep g4-tier1 --out artifacts \
		--scratch artifacts/g4-scratch --sample-size 1000000
	# convert artifacts/g4-scratch/tier2_candidates.json's reservoir
	# sample to a plain word list here, then:
	./target/release/silica-sweep g4-disasm --words artifacts/g4-scratch/tier2-words.txt \
		--spec-decode-table artifacts/decode-table.bin --out artifacts/tier2-disasm
	micromamba run -p ./.venv python -m pysilica.analyze.g4_run \
		--tier1-dir artifacts/g4-scratch --tier2-disasm artifacts/tier2-disasm \
		--validity-disagreements 723801678 \
		--text-tier-sample-size 1000000 \
		--text-tier-population 1266064016
	micromamba run -p ./.venv python -m pysilica.analyze.g5_report
	micromamba run -p ./.venv python -m pysilica.analyze.g6_reproducers
	micromamba run -p ./.venv python scripts/compute_result_hash.py
