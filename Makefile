PYTHON ?= python3

.PHONY: install-dev test lint format check package-smoke

install-dev:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

package-smoke:
	tmpdir=$$(mktemp -d); \
	trap 'rm -rf "$$tmpdir"' EXIT; \
	artifacts_dir="$$tmpdir/dist"; \
	venv_dir="$$tmpdir/venv"; \
	cd "$$tmpdir" && $(PYTHON) -m build --outdir "$$artifacts_dir" "$(CURDIR)"; \
	$(PYTHON) -m venv "$$venv_dir"; \
	"$$venv_dir/bin/python" -m pip install --upgrade pip; \
	"$$venv_dir/bin/python" -m pip install "$$artifacts_dir"/signal_agent-*.whl; \
	list_output=$$("$$venv_dir/bin/signal-agent" --list-tasks); \
	printf '%s\n' "$$list_output"; \
	printf '%s\n' "$$list_output" | grep -Fx job_search >/dev/null; \
	"$$venv_dir/bin/python" -c "from app.scorers import load_job_search_profile; profile = load_job_search_profile(); assert profile.target_roles"

check: test lint package-smoke
