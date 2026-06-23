# signal-agent

`signal-agent` is a personal Python agent for collecting and ranking signals
such as job openings and Taiwan stock mover reports, then sending Telegram
digests.

## Status

This repository is in bootstrap mode. It includes a working Python entrypoint,
sample configuration files, and the initial package layout for future source
collectors, scoring, and notifier integrations.

It now includes early best-effort job collectors for `job104`, `indeed`,
`glassdoor`, and `linkedin_email`. Some live runs can still be blocked by
provider anti-bot challenges or missing email credentials, so the fetch
pipeline persists successful sources and records source-level failures without
aborting the whole run.

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
python main.py --task job_search
python main.py --task tw_stock_movers
python main.py --telegram-test
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
app/profiles/        # packaged job-scoring profiles
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
│   │   ├── secrets.py
│   │   └── time_utils.py
│   ├── notifiers/
│   │   └── telegram.py
│   ├── profiles/
│   │   └── henry_job_profile.md
│   ├── scorers/
│   │   ├── job_rule_scorer.py
│   │   └── job_search.py
│   ├── sources/
│   │   ├── job104.py
│   │   ├── indeed.py
│   │   ├── glassdoor.py
│   │   └── linkedin_email.py
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
- `python main.py --task fetch` runs the configured job-search sources,
  persists results to SQLite, and prints a source-by-source summary.
- `python main.py --task job_search` runs the full MVP pipeline: fetch jobs,
  deduplicate, score new jobs, persist results, and send a Telegram digest when
  there is useful output.
- `python main.py --task tw_stock_movers` runs the Taiwan stock-mover task
  during market hours, persists `tw_stock_mover` signals, and reports the top
  configured gainers and losers from the live TWSE source for listed stocks.
- `python main.py --telegram-test` sends a one-off Telegram test message using
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Telegram notifier

`app/notifiers/telegram.py` sends plain-text messages through the Telegram Bot API.
Long payloads are split into Telegram-safe chunks automatically.

```python
from app.core import load_env_settings
from app.notifiers import build_telegram_notifier

env = load_env_settings()
notifier = build_telegram_notifier(env=env)
notifier.send_message("Daily job digest\n- 3 new matches")
```

Use `python main.py --telegram-test "hello from signal-agent"` for a manual smoke test.

## Source plugins

Each data source implements `BaseSource` in `app/sources/` and returns normalized
`SignalItem` objects from `fetch()`. Use `fetch_from_sources()` to run one or more
sources through the bootstrap pipeline. `DummySource` provides stable in-memory
samples for local development and tests, while `job104`, `indeed`, and
`glassdoor` provide live job-search integrations. `linkedin_email` parses
LinkedIn job-alert emails from a local `.eml` file or IMAP inbox without
requiring a LinkedIn browser login. The global search sources default to the
Taiwan region when `tasks.job_search.locations` is not set. `twse` fetches live
listed-stock quotes from the official TWSE market data endpoints and normalizes
them into `tw_stock_mover` signals.

`job104` uses 104's public JSON search API over HTTP (no browser automation).
When `locations` are configured, the MVP folds them into the keyword query
string until explicit 104 area codes are configured. Live fetches can still be
blocked by anti-bot challenges that return HTML instead of JSON; the fetch task
records source-level failures and continues with other configured sources.

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

`python main.py --task fetch` runs the configured `tasks.job_search.sources`,
persists each successful source to SQLite, and skips unsupported or failed
sources without aborting the whole run.

`python main.py --task job_search` scores newly inserted jobs with the optional
OpenAI-backed scorer when `scoring.llm_enabled` is true, and falls back to the
rule-based scorer if the API request fails or returns invalid structured JSON.
Duplicate jobs are still stored. Unscored duplicates from earlier `fetch` runs
are scored on the next `job_search` run, but jobs that were already reported in
a digest are not rescored or included again.

`python main.py --task tw_stock_movers` checks `is_tw_market_open()` before it
fetches. While the market is open, it persists only `tw_stock_mover` items,
ignores non-stock signals from mixed sources such as `dummy`, and optionally
delivers a Telegram digest with separate top gainer and top loser sections.

## Taiwan market hours

`tw_stock_movers` workflows should call `is_tw_market_open()` before fetching or
sending reports so frequent schedulers can no-op outside 台股開盤時間:

```python
from app.core import is_tw_market_open

if not is_tw_market_open():
    print("tw market closed; skipping stock mover run")
    return
```

The MVP guard treats Monday through Friday, 09:00 through 13:30 inclusive, in
`Asia/Taipei`. Public holidays are not handled yet.

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
      - indeed
      - glassdoor
      - linkedin_email
    keywords:
      - python
      - backend
      - agent
    locations:
      - Taiwan
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
  llm_model: gpt-4o-mini
```

Load config in code with:

```python
from app.core.config import load_config, load_env_settings

config = load_config("config.yaml")
env = load_env_settings()
```

- Put local secrets in `.env`, not in `config.yaml` or committed files.
- Set `OPENAI_API_KEY` in `.env` before enabling `scoring.llm_enabled: true`.
- Set `scoring.llm_model` in `config.yaml` to choose which OpenAI model powers
  the optional LLM scorer.
- Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` before using
  `--telegram-test` or the Telegram notifier.
- For `linkedin_email`, either set `LINKEDIN_EMAIL_SOURCE_PATH` to a local
  `.eml` file or directory, or set `LINKEDIN_EMAIL_IMAP_HOST`,
  `LINKEDIN_EMAIL_IMAP_USERNAME`, and `LINKEDIN_EMAIL_IMAP_PASSWORD`. Optional:
  `LINKEDIN_EMAIL_IMAP_MAILBOX` defaults to `INBOX`.
- `.env.example` documents the expected local secret variables.
- `deploy/k3s/secret.example.yaml` shows the Kubernetes Secret shape for later
  deployment work.

## Job Profile

Job matching reads a human-edited Markdown profile at
`app/profiles/henry_job_profile.md`. The file ships with the package, so
`load_job_search_profile()` works from editable installs and wheels. Keep the
section headings below and use short bullets so the rule-based scorer can parse
them consistently.

Expected headings:

```text
## Target roles
## Strong skills
## Preferred companies / industries
## Avoid roles
## Reject keywords
## Location preferences
## Remote / hybrid preferences
## Notes for scorer
```

```python
from datetime import UTC, datetime

from app.core import SignalItem
from app.scorers import JobRuleScorer, load_job_search_profile, score_job_posting

profile = load_job_search_profile()
scorer = JobRuleScorer(profile)
item = SignalItem(
    source="manual",
    item_type="job",
    title="Cloud SDET",
    observed_at=datetime.now(UTC),
    content="Remote Python and Pytest automation with Kubernetes.",
)
result = score_job_posting(item, profile)
rule_result = scorer.score(item)
print(result.score, result.matched_positive_signals)
print(rule_result.score, rule_result.recommendation, rule_result.reason)
```

- Update the bullets when target roles, strong skills, preferred industries, or
  reject signals change.
- Keep the file free of secrets, phone numbers, and private credentials.
- `Notes for scorer` is stored for human and optional LLM guidance; the
  rule-based scorer does not use it.
- `score_job_posting()` is a lower-level bootstrap float scorer used by tests
  and future ranking flows. Prefer `JobRuleScorer` for the offline MVP output.
- `JobRuleScorer` is the offline MVP scorer that returns a clamped 0-100 score,
  recommendation, and human-readable reasons without using `OPENAI_API_KEY`.

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
