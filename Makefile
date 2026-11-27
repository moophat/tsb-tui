# The extracted TUI's repository gates. Missing inputs fail; ruff is optional only outside CI.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := lint
PYTHON ?= python3
export PYTHONDONTWRITEBYTECODE := 1

.PHONY: contract-init contract-copy contract-check contract-copy-check test lint
contract-init:
	@bash tools/contract-pin.sh init

contract-copy:
	@bash tools/contract-pin.sh copy

contract-check:
	@bash tools/contract-pin.sh check

contract-copy-check:
	@bash tools/contract-pin.sh copy-check

test: contract-copy-check
	@test -d tests
	# NO_COLOR strips SVG colors that the visual tests need to verify.
	@env -u NO_COLOR $(PYTHON) -m pytest tests/ -p no:cacheprovider

lint:
	@for input in tsb_tui tests bench tools .github/scripts/tests; do test -d "$$input"; done
	@if command -v ruff >/dev/null 2>&1; then \
	  ruff check tsb_tui tests bench tools; \
	elif [ -n "$${CI:-}" ]; then \
	  echo "ERROR: ruff is required in CI (CI=$$CI) and is not installed" >&2; exit 1; \
	else \
	  echo "SKIP: ruff — not installed, Python style not checked (pip install ruff)"; \
	fi
	@$(PYTHON) -m pytest .github/scripts/tests tools/test_import_lint.py -p no:cacheprovider
	@$(PYTHON) tools/import_lint.py
	@$(PYTHON) .github/scripts/docs_branch_guard.py
	@bash .github/scripts/leak-check.sh
	@PYTHON="$(PYTHON)" bash tools/check-engine.sh
