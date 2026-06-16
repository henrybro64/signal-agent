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
python main.py --task dummy
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
- `python main.py --task dummy` loads `config.yaml`, reads `.env`, and prints a
  short bootstrap summary that proves the entrypoint wiring works.

## Configuration

- `config.yaml` is loaded by bootstrap tasks through `app.config.load_config`.
- `.env.example` documents notifier environment variables loaded by
  `app.config.load_env_settings`.

## Development

Use either the documented commands directly or the `Makefile` targets:

```bash
python -m pip install -e '.[dev]'
python -m pytest
ruff check .
ruff format .
make test
make lint
make format
```

If your Ubuntu environment exposes `python3` instead of `python`, use
`python3` consistently or run the `Makefile` targets directly.
