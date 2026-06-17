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
│   ├── config.py
│   ├── notifiers/
│   ├── scorers/
│   ├── sources/
│   └── tasks/
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

## Configuration

- `config.yaml` is loaded by bootstrap tasks through `app.config.load_config`.
- `.env.example` documents notifier environment variables loaded by
  `app.config.load_env_settings`.

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
