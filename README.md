# signal-agent

`signal-agent` is a personal Python agent for collecting and ranking signals
such as job openings and Taiwan stock mover reports, then sending Telegram
digests.

Coding agents should follow [`AGENTS.md`](AGENTS.md) for architecture,
coding rules, completed MVP scope (#1–#25), and the recommended M6 execution
order. Operator docs stay in this README.

## Status

MVP workflows for job search and Taiwan stock movers are implemented, with
Docker Compose and k3s CronJob deployment paths. Open work focuses on M6
hardening and later agent-platform evolution; see `AGENTS.md`.

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
python main.py --health
python main.py --recent-runs --limit 5
python main.py --task dummy
python main.py --task fetch
python main.py --task job_search
python main.py --task tw_stock_movers
python main.py --telegram-test
signal-agent --help
python -m pytest
```

## Docker Compose

Use Docker Compose for an Ubuntu VM when you want a simple containerized run
path before moving to k3s.

For unattended VM scheduling (cron or systemd), follow the full runbook:
[`docs/ubuntu-vm-deployment.md`](docs/ubuntu-vm-deployment.md). Helper scripts
live under `deploy/ubuntu/`. **k3s is optional** and not required for MVP.

```bash
cp .env.example .env
mkdir -p data logs
docker compose build
docker compose run --rm signal-agent python main.py --task dummy
docker compose run --rm signal-agent python main.py --task fetch
docker compose run --rm signal-agent python main.py --task job_search
docker compose run --rm signal-agent python main.py --task tw_stock_movers
docker compose run --rm signal-agent python main.py --telegram-test
```

Compose behavior:

- Create `.env` from `.env.example` before the first `docker compose run`
  (Compose loads `.env` via `env_file`).
- Rebuild the image after pulling code changes: `docker compose build`.
- `config.yaml` is mounted read-only at `/app/config.yaml`.
- SQLite data persists in `./data`, so the default database lives at
  `/app/data/signals.sqlite3`.
- The image bakes in the application code, but task commands remain
  override-friendly through `docker compose run --rm signal-agent ...`.
- If you want dry runs without any Telegram delivery, set both
  `notifiers.telegram.enabled: false` and
  `notifiers.telegram.failure_alerts_enabled: false` in `config.yaml`.
- Set `notifiers.telegram.failure_alerts_enabled: false` if you want to keep
  Telegram digests enabled but suppress task-failure alerts.
- If you use `LINKEDIN_EMAIL_SOURCE_PATH`, point it to a file under a mounted
  path such as `/app/data/...`, or add another volume mount.

## k3s CronJobs

Optional. Prefer the Ubuntu VM + Compose path
([`docs/ubuntu-vm-deployment.md`](docs/ubuntu-vm-deployment.md)) for MVP.

Use the manifests in `deploy/k3s/` after you have a `signal-agent` image built
from the repository `Dockerfile`.

```bash
cp deploy/k3s/secret.example.yaml deploy/k3s/secret.yaml
# Edit deploy/k3s/secret.yaml and replace the placeholder values.
kubectl apply -f deploy/k3s/namespace.yaml \
  -f deploy/k3s/configmap.yaml \
  -f deploy/k3s/pvc.yaml \
  -f deploy/k3s/secret.yaml \
  -f deploy/k3s/cronjob-job-search.yaml \
  -f deploy/k3s/cronjob-tw-stock-movers.yaml
```

k3s deployment behavior:

- The manifests create a dedicated `signal-agent` namespace.
- Copy `secret.example.yaml` to `secret.yaml`, replace the `replace-me` values,
  and apply `secret.yaml` only. The example file is not meant for production use.
- `configmap.yaml` mounts the same `config.yaml` content at `/app/config.yaml`.
- `pvc.yaml` persists SQLite data at `/app/data` for both CronJobs.
- `cronjob-job-search.yaml` runs `job_search` at 08:05 and 18:05 in
  `Asia/Taipei`.
- `cronjob-tw-stock-movers.yaml` runs `tw_stock_movers` every 10 minutes on
  weekdays in `Asia/Taipei`, while the task still guards Taiwan market hours
  internally.
- Job search is offset to `:05` so it does not start at the same minute as the
  stock-mover CronJob on the shared SQLite volume.
- `cronjob-job-search.yaml` sets `activeDeadlineSeconds: 1800` so a hung source
  (for example `linkedin_email` IMAP) cannot block later schedules forever
  under `concurrencyPolicy: Forbid`.
- Both CronJobs set `spec.timeZone: Asia/Taipei` so schedules do not depend on
  the controller manager timezone.
- The CronJobs use `signal-agent:local` with `imagePullPolicy: IfNotPresent`;
  change the image reference if your k3s nodes pull from a registry instead.

## Architecture

`signal-agent` is organized around independent workflows that collect signals,
score them, and send digests:

```text
config.yaml          # non-secret runtime settings
.env                 # secrets such as OpenAI and Telegram credentials
docker-compose.yml   # Ubuntu VM task runner with mounted config and data
Dockerfile           # runtime image used by Docker Compose
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
├── AGENTS.md            # Codex / coding-agent implementation map
├── README.md
├── .env.example
├── .dockerignore
├── .gitignore
├── Dockerfile
├── config.yaml
├── docker-compose.yml
├── main.py
├── pyproject.toml
├── app/
│   ├── core/
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── error_handling.py
│   │   ├── logging.py
│   │   ├── models.py
│   │   ├── secrets.py
│   │   └── time_utils.py
│   ├── notifiers/
│   │   └── telegram.py
│   ├── profiles/
│   │   └── henry_job_profile.md
│   ├── scorers/
│   │   ├── job_llm_scorer.py
│   │   ├── job_rule_scorer.py
│   │   └── job_search.py
│   ├── sources/
│   │   ├── job104.py
│   │   ├── indeed.py
│   │   ├── glassdoor.py
│   │   ├── linkedin_email.py
│   │   ├── tpex.py
│   │   └── twse.py
│   └── tasks/
├── docs/
│   └── ubuntu-vm-deployment.md
├── deploy/
│   ├── ubuntu/
│   │   ├── crontab.example
│   │   ├── run-job-search.sh
│   │   ├── run-task.sh
│   │   ├── run-tw-stock-movers.sh
│   │   └── systemd/
│   └── k3s/
│       ├── configmap.yaml
│       ├── cronjob-job-search.yaml
│       ├── cronjob-tw-stock-movers.yaml
│       ├── namespace.yaml
│       ├── pvc.yaml
│       └── secret.example.yaml
├── sql/
│   └── schema.sql
└── tests/
```

## Bootstrap CLI

- `python main.py --help` or `signal-agent --help` shows the available options.
- `python main.py --list-tasks` shows the registered CLI task names.
- `python main.py --health` prints a concise SQLite health summary for recent
  scheduled task runs, failed sources, and Telegram credential presence. Market-
  closed stock runs show as `skipped (taiwan_market_closed)`. It does not fetch
  external sources or send Telegram messages.
- `python main.py --recent-runs --limit 20` prints the newest source collection
  rows from `signal_runs`.
- `python main.py --task dummy` loads `config.yaml`, reads `.env`, and prints a
  short bootstrap summary that proves the entrypoint wiring works.
- `python main.py --task fetch` runs the configured job-search sources,
  persists results to SQLite, and prints a source-by-source summary.
- `python main.py --task job_search` runs the full MVP pipeline: fetch jobs,
  deduplicate, score new jobs, persist results, and send a Telegram digest when
  there is useful output.
- `python main.py --task tw_stock_movers` runs the Taiwan stock-mover task
  during market hours, persists `tw_stock_mover` signals, and reports separate
  top gainers and losers for TWSE listed (上市) and TPEx OTC (上櫃) stocks.
- `python main.py --telegram-test` sends a one-off Telegram test message using
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Structured logging

`fetch`, `job_search`, and `tw_stock_movers` emit JSON log lines to stdout so
Docker and k3s can collect task, source, and notification events from container
logs. Human-readable summaries are still printed for local CLI use.

Typical fields:

- `event` such as `task_started`, `source_run_completed`, `notification_sent`
- `task_name`, `task_run_id`, `source_name`, and SQLite `run_id` for source runs
- `fetched_count`, `new_count`, `duplicate_count`, `skipped_count`
- `notification_status` and `notified_count`
- `stack_trace` on failures

Example:

```json
{
  "event": "source_run_completed",
  "level": "info",
  "message": "Source run completed",
  "task_name": "job_search",
  "source_name": "job104",
  "run_id": 12,
  "fetched_count": 8,
  "new_count": 3,
  "duplicate_count": 2,
  "skipped_count": 5
}
```

Secrets, tokens, and raw email content are not logged.

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
Taiwan region when `tasks.job_search.locations` is not set. `twse` and `tpex`
fetch live Taiwan listed-stock and OTC quotes from the official market data
endpoints and normalize them into `tw_stock_mover` signals.

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
delivers a Telegram digest with separate 上市 and 上櫃 top gainer/loser sections.
Each scheduled run stores a minute-bucketed snapshot in SQLite so a 10-minute
cron can build price-move history without overwriting earlier rows.

Telegram output is in Traditional Chinese:

```text
台股漲跌幅快報 2026-06-12 10:10

上市前20漲幅
1. 2330 台積電 +3.51%
...

上市前20跌幅
...

上櫃前20漲幅
...

上櫃前20跌幅
...

資料來源異常
- 上櫃資料：quote provider timeout
```

If one market source fails, the digest still includes the successful market and
adds a short warning under `資料來源異常`.

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

Run history now has two levels:

- `task_runs` stores one row per top-level CLI task execution with `running`,
  `success`, or `failed` status plus fetched/new/skipped/notified counts.
- `signal_runs` keeps the existing source-level run detail for each provider
  inside a task, including per-source failures that do not always abort the
  whole task.

`task_runs.status` starts as `running` when a task begins and is finalized to
`success` or `failed` when the task exits. If a process is killed mid-run, the
row can remain `running`. A market-closed `tw_stock_movers` skip is recorded as
`success` with zero counts because the no-op exit is intentional.

Telegram digests optionally append a run summary:

- `job_search` adds an English `run summary: status=...` footer.
- `tw_stock_movers` adds a Traditional Chinese `執行摘要：...` footer.

```python
from app.core import (
    SignalItem,
    finish_signal_run,
    finish_task_run,
    init_db,
    start_signal_run,
    start_task_run,
    upsert_signal,
)
from datetime import UTC, datetime

init_db("data/signals.sqlite3")
task_run_id = start_task_run("fetch")
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
finish_task_run(
    task_run_id,
    status="success",
    fetched_count=1,
    new_count=1 if result.action == "inserted" else 0,
    skipped_count=1 if result.action == "updated" else 0,
    notified_count=0,
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
    failure_alerts_enabled: true

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
- `notifiers.telegram.failure_alerts_enabled` controls best-effort Telegram
  alerts when a top-level task exits with an error. If omitted, it defaults to
  the same value as `notifiers.telegram.enabled`.
- For `linkedin_email`, either set `LINKEDIN_EMAIL_SOURCE_PATH` to a local
  `.eml` file or directory, or set `LINKEDIN_EMAIL_IMAP_HOST`,
  `LINKEDIN_EMAIL_IMAP_USERNAME`, and `LINKEDIN_EMAIL_IMAP_PASSWORD`. Optional:
  `LINKEDIN_EMAIL_IMAP_MAILBOX` defaults to `INBOX`.
- `.env.example` documents the expected local secret variables.
- `deploy/k3s/secret.example.yaml` shows the Kubernetes Secret shape. Copy it to
  `deploy/k3s/secret.yaml`, fill in real values, and apply that file in k3s.

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

GitHub Actions CI runs on every pull request and on pushes to `main`. The
workflow installs Python 3.12, then runs the same checks as local development:

- `make test` (`pytest`)
- `make lint` (`ruff check .`)
- `make package-smoke` (build wheel, install it, verify `signal-agent --list-tasks`
  and bundled profile data)

Use `make check` locally before opening a PR; it runs all three targets in
order.

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
