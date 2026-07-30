.PHONY: check fmt lint test verify doctor hooks-test all

check:
	cargo fmt --check
	cargo clippy --workspace -- -D warnings
	cargo test --workspace
	micromamba run -p ./.venv ruff check .
	micromamba run -p ./.venv mypy --strict pysilica
	micromamba run -p ./.venv lint-imports
	micromamba run -p ./.venv pytest -q
	micromamba run -p ./.venv python scripts/no_docstrings.py
	! grep -rnE '^\s*(///|//!)' crates/
	micromamba run -p ./.venv python scripts/check_hooks.py
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
all:
	cargo build --release -p silica-sweep
	micromamba run -p ./.venv python -m pysilica.cli compile-spec
	for shard in $$(seq 0 255); do \
		./target/release/silica-sweep run --shard $$shard \
			--spec-decode-table artifacts/decode-table.bin --out artifacts; \
	done
	micromamba run -p ./.venv python -m pysilica.analyze.g4_run \
		--tier1-dir artifacts/tier1 --tier2-disasm artifacts/tier2-disasm \
		--validity-disagreements $$(cat artifacts/validity_disagreements.txt) \
		--text-tier-sample-size $$(cat artifacts/text_tier_sample_size.txt) \
		--text-tier-population $$(cat artifacts/text_tier_population.txt)
	micromamba run -p ./.venv python -m pysilica.analyze.g5_report
	micromamba run -p ./.venv python -m pysilica.analyze.g6_reproducers
	micromamba run -p ./.venv python scripts/compute_result_hash.py
