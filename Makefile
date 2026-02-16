.PHONY: check fmt lint test verify doctor hooks-test

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
	micromamba run -p ./.venv python -m pysilica.cli verify

doctor:
	micromamba run -p ./.venv python -m pysilica.cli doctor

verify:
	micromamba run -p ./.venv python -m pysilica.cli verify
