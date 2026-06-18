# signal-agent

`signal-agent` is a personal Python agent for collecting and ranking signals
such as job openings and Taiwan stock mover reports, then sending Telegram
digests.

## Status

This repository is in bootstrap mode. It includes a working Python entrypoint,
sample configuration files, and the initial package layout for future source
collectors, scoring, and notifier integrations.

The bootstrap intentionally does not include live collectors, Docker, or LLM
logic yet.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python main.py --help
python main.py --list-tasks
python main.py --task dummy
python main.py --task fetch
signal-agent --help
python -m pytest
```

## Architecture

`signal-agent` is organized around independent workflows that collect signals,
score them, and send digests:

```text
config.yaml          # non-secret runtime settings
.env                 # secrets such as OpenAI and Telegram credentials
app/core/            # shared models, config, secrets, and SQLite storage
app/sources/         # collectors that return SignalItem objects
app/tasks/           # workflows such as job_search and tw_stock_movers
app/scorers/         # ranking logic
app/notifiers/       # outbound delivery such as Telegram
sql/                 # SQLite schema for signals and run history
```

- **Config** (`config.yaml`) describes what the agent should do.
- **Sources** fetch normalized `SignalItem` data from one provider.
- **Tasks** orchestrate one workflow end to end.
- **Storage** persists signals, deduplicates repeats, and tracks status.
- **Secrets** stay in environment variables, not in git.

## Project Layout

```text
signal-agent/
├── README.md
├── .env.example
├── .gitignore
├── config.yaml
├── main.py
├── pyproject.toml
├── app/
│   ├── core/
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── models.py
│   │   └── secrets.py
│   ├── notifiers/
│   ├── scorers/
│   ├── sources/
│   └── tasks/
├── deploy/
│   └── k3s/
│       └── secret.example.yaml
├── sql/
│   └── schema.sql
└── tests/
```

## Bootstrap CLI

- `python main.py --help` or `signal-agent --help` shows the available options.
- `python main.py --list-tasks` shows the registered CLI task names.
- `python main.py --task dummy` loads `config.yaml`, reads `.env`, and prints a
  short bootstrap summary that proves the entrypoint wiring works.
- `python main.py --task fetch` runs the dummy source plugin and prints fetched
  `SignalItem` titles.

## Source plugins

Each data source implements `BaseSource` in `app/sources/` and returns normalized
`SignalItem` objects from `fetch()`. Use `fetch_from_sources()` to run one or more
sources through the bootstrap pipeline. `DummySource` provides stable in-memory
samples for local development and tests.

```python
from app.sources import DummySource, fetch_from_sources

items = fetch_from_sources([DummySource()])
```

## Task runner

Each workflow implements `BaseTask` in `app/tasks/` and executes from the CLI
through the task registry. Register new tasks in `app/tasks/__init__.py`, then
run them with `--task` or list them with `--list-tasks`.

```python
from app.tasks import TASKS, build_task

task = build_task("dummy", config_path="config.yaml")
task.run()
```

## Storage

Signals are persisted in SQLite through `app/core/db.py`. Local databases typically
live under `data/` and are ignored by git.

```python
from app.core import SignalItem, finish_signal_run, init_db, start_signal_run, upsert_signal
from datetime import UTC, datetime

init_db("data/signals.sqlite3")
run_id = start_signal_run("linkedin")
result = upsert_signal(
    SignalItem(
        source="linkedin",
        item_type="job",
        title="Backend Engineer",
        observed_at=datetime.now(UTC),
        url="https://example.com/jobs/1",
        content="Build backend services.",
    )
)
finish_signal_run(
    run_id,
    status="completed",
    fetched_count=1,
    inserted_count=1 if result.action == "inserted" else 0,
    updated_count=1 if result.action == "updated" else 0,
)
```

Dedup priority is:

1. `url`
2. `(source, external_id)`
3. `content_hash`

## Configuration

`config.yaml` holds non-secret runtime settings. Secrets such as
`OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` are loaded from
`.env` through `load_env_settings()`.

Example shape:

```yaml
agent:
  timezone: Asia/Taipei
  digest_time: "20:00"

tasks:
  job_search:
    enabled: true
    sources:
      - job104
      - linkedin_email
    keywords:
      - python
      - backend
      - agent
  tw_stock_movers:
    enabled: true
    top_n: 20
    timezone: Asia/Taipei

notifiers:
  telegram:
    enabled: true

scoring:
  freshness_weight: 0.6
  priority_weight: 0.4
  llm_enabled: false
```

Load config in code with:

```python
from app.core.config import load_config, load_env_settings

config = load_config("config.yaml")
env = load_env_settings()
```

- Put local secrets in `.env`, not in `config.yaml` or committed files.
- Set `OPENAI_API_KEY` in `.env` before enabling `scoring.llm_enabled: true`.
- `.env.example` documents the expected local secret variables.
- `deploy/k3s/secret.example.yaml` shows the Kubernetes Secret shape for later
  deployment work.

## Development

Use either the documented commands directly or the `Makefile` targets:

```bash
make install-dev
make test
make lint
make format
make check
```

Equivalent direct commands:

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest
python3 -m ruff check .
python3 -m ruff format .
```

The `Makefile` defaults to `python3`, which matches typical Ubuntu setups. To
use a different interpreter, run `make test PYTHON=python3.12`.
