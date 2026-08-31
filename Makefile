# email-reply-extractor — developer tasks.
#
# The Python tools run from ./.venv when the tree has one and from PATH
# otherwise. Force either with `make test VENV_BIN=` or
# `make test VENV_BIN=.venv/bin/`.
VENV_BIN ?= $(if $(wildcard .venv/bin/pytest),.venv/bin/,)

.PHONY: test lint

test:
	$(VENV_BIN)pytest -q

lint:
	$(VENV_BIN)ruff check .
	$(VENV_BIN)ruff format --check .
