# email-reply-extractor — developer tasks for all three ports.
#
# Run every target from the repository root. `make test` and `make lint` cover
# each port whose directory exists; the per-port targets run one port.
#
# The Python tools run from ./.venv when the tree has one and from PATH
# otherwise. Force either with `make test VENV_BIN=` or
# `make test VENV_BIN=$(pwd)/.venv/bin/`.
VENV_BIN ?= $(if $(wildcard .venv/bin/pytest),$(CURDIR)/.venv/bin/,)
PYTHON ?= $(if $(VENV_BIN),$(VENV_BIN)python,python3)

PORTS := python $(if $(wildcard typescript/package.json),typescript) $(if $(wildcard rust/Cargo.toml),rust)

.PHONY: test lint test-python lint-python test-typescript lint-typescript \
	test-rust lint-rust vectors check-vectors check-sync

test: $(addprefix test-,$(PORTS))

lint: $(addprefix lint-,$(PORTS))

test-python:
	cd python && $(VENV_BIN)pytest -q

lint-python:
	cd python && $(VENV_BIN)ruff check .
	cd python && $(VENV_BIN)ruff format --check .

test-typescript:
	cd typescript && npm test

lint-typescript:
	cd typescript && npm run lint

test-rust:
	cd rust && cargo test

lint-rust:
	cd rust && cargo fmt --check
	cd rust && cargo clippy --all-targets -- -D warnings

# Regenerate conformance/ (everything except its README) from the Python
# reference implementation.
vectors:
	$(PYTHON) python/tools/conformance.py

# Regenerate into a temporary directory and fail if conformance/ differs.
check-vectors:
	$(PYTHON) python/tools/conformance.py --check

# Package version, EXTRACTION_VERSION and LICENSE copies agree across ports.
check-sync:
	$(PYTHON) python/tools/check_sync.py
