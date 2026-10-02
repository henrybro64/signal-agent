PYTHON ?= python3

.PHONY: install-dev test lint format check package-smoke eval

install-dev:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

eval:
	$(PYTHON) -m app.evaluation --suite core

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
	printf '%s\n' "$$list_output" | grep -Fx job_research_agent >/dev/null; \
	printf '%s\n' "$$list_output" | grep -Fx tw_market_assistant >/dev/null; \
	printf '%s\n' "$$list_output" | grep -Fx tw_stock_movers >/dev/null; \
	printf '%s\n' "$$list_output" | grep -Fx stock_watchlist >/dev/null; \
	"$$venv_dir/bin/python" -c "from app.scorers import load_job_search_profile; profile = load_job_search_profile(); assert profile.target_roles"; \
	"$$venv_dir/bin/python" -c "from datetime import date; from app.core import is_tw_market_holiday, load_tw_market_holiday_dates; assert date(2026, 1, 1) in load_tw_market_holiday_dates(); assert is_tw_market_holiday(date(2026, 1, 1))"; \
	"$$venv_dir/bin/python" -c "from pathlib import Path; import tempfile; from app.core import init_db, read_schema_version; from app.core.migrations import latest_migration_version, list_migrations; assert list_migrations(); db=Path(tempfile.mkdtemp())/'db.sqlite3'; init_db(db); assert read_schema_version(db)==latest_migration_version()"

check: test lint eval package-smoke
