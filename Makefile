PYTHON ?= python3

.PHONY: install-dev test lint format check

install-dev:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest

lint:
	ruff check .

format:
	ruff format .

check: test lint
