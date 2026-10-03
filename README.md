# signal-agent

`signal-agent` is a personal Python agent for collecting and ranking signals
such as job openings and Taiwan stock mover reports, then sending Telegram
digests.

Coding agents should follow [`AGENTS.md`](AGENTS.md) for architecture,
coding rules, completed MVP/M6 scope, and the recommended M7 execution order.
The LangChain-ready platform roadmap lives in
[`docs/m7-agent-platform-roadmap.md`](docs/m7-agent-platform-roadmap.md).
Operator docs stay in this README.

## Status

MVP workflows for job search and Taiwan stock movers are implemented, with
Docker Compose and k3s CronJob deployment paths. M6 hardening (#49–#57) and
M7 agent platform pieces (#60–#67) are on `main`. M8 adds an OpenClaw-like
local product CLI (`setup`, runtime lifecycle, and `chat`); see `AGENTS.md`.

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

# First-run wizard (writes config.yaml + .env, initializes SQLite)
signal-agent setup --non-interactive --provider fake
signal-agent doctor
signal-agent config show

# Unified CLI (`--root` / `--config` work before or after the subcommand)
signal-agent tasks
signal-agent run dummy
signal-agent status
signal-agent chat
signal-agent start   # long-running worker (survives individual tick failures)
signal-agent stop

# Legacy flags still work during migration
python main.py --list-tasks
python main.py --task job_search
python main.py --health
```

`setup` never prints API keys. Re-running setup keeps existing `.env` secrets
and comments unless you pass `--force` (which writes a `.bak` backup first).
Optional Telegram/browser/MCP integrations can stay disabled.

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
- `pvc.yaml` persists SQLite data at `/app/data` for the CronJobs.
- `cronjob-job-search.yaml` runs `job_search` at 08:05 and 18:05 in
  `Asia/Taipei`.
- `cronjob-tw-stock-movers.yaml` runs `tw_stock_movers` every 10 minutes on
  weekdays in `Asia/Taipei`, while the task still guards Taiwan market hours
  internally. The same CronJob also runs `stock_watchlist` (no-op while that
  task remains disabled in config).
- Job search is offset to `:05` so it does not start at the same minute as the
  stock CronJob on the shared SQLite volume.
- `cronjob-job-search.yaml` sets `activeDeadlineSeconds: 1800` so a hung source
  (for example `linkedin_email` IMAP) cannot block later schedules forever
  under `concurrencyPolicy: Forbid`.
- CronJobs set `spec.timeZone: Asia/Taipei` so schedules do not depend on
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
│   │   ├── time_utils.py
│   │   └── tw_market_calendar.py
│   ├── data/
│   │   └── tw_market_holidays.yaml
│   ├── notifiers/
│   │   └── telegram.py
│   ├── profiles/
│   │   └── henry_job_profile.md
│   ├── scorers/
│   │   ├── job_llm_scorer.py
│   │   ├── job_rule_scorer.py
│   │   └── job_search.py
│   ├── llm/                 # framework-neutral LLM gateway
│   ├── sources/
│   │   ├── job104.py
│   │   ├── indeed.py
│   │   ├── glassdoor.py
│   │   ├── linkedin_email.py
│   │   ├── tpex.py
│   │   └── twse.py
│   └── tasks/
├── docs/
│   ├── ubuntu-vm-deployment.md
│   └── m7-agent-platform-roadmap.md
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
├── app/sql/migrations/
│   └── 001_initial.sql
└── tests/
```

## Bootstrap CLI

- `python main.py --help` or `signal-agent --help` shows the available options.
- `python main.py --list-tasks` shows the registered CLI task names.
- `python main.py --health` prints a concise SQLite health summary for recent
  scheduled task runs, failed sources, and Telegram credential presence. Market-
  closed stock runs show as `skipped (taiwan_market_closed)`. Interrupted
  (`running`) or stale latest runs (no fresh schedule activity) mark
  `Overall: DEGRADED`. It does not fetch external sources or send Telegram
  messages.
- `python main.py --recent-runs --limit 20` prints the newest source collection
  rows from `signal_runs`.
- `python main.py --task dummy` loads `config.yaml`, reads `.env`, and prints a
  short bootstrap summary that proves the entrypoint wiring works.
- `python main.py --task fetch` runs the configured job-search sources,
  persists results to SQLite, and prints a source-by-source summary.
- `python main.py --task job_search` runs the full MVP pipeline: fetch jobs,
  deduplicate, score new jobs, persist results, and send a Telegram digest when
  there is useful output. Prefer this for scheduled production digests.
- `python main.py --task job_research_agent` runs the bounded M7 pilot workflow
  (tool allowlist + optional LangGraph when `.[langchain]` is installed). Use
  it for experimentation; it does not replace `job_search`. Telegram delivery
  stays off unless `tasks.job_research_agent.telegram_enabled: true`. The
  LangGraph path uses the optional install for graph orchestration; #63
  ChatModel/tool adapters are available separately and are not required for
  the fixed pilot graph. When memory documents are ingested into the shared
  SQLite DB under the `job_research` namespace, the digest can include cited
  evidence snippets.
- `python main.py --eval` (or `make eval` / `signal-agent-eval`) runs the
  offline CI evaluation suite packaged under `app/evaluation/cases/` with fake
  LLM/tools and redacted traces. No network or paid APIs.
- `python main.py --mcp-serve` exposes allowlisted read-only tools
  (`search_jobs`, `get_tw_stock_movers`, `query_recent_runs`) over MCP on
  localhost after `pip install -e '.[mcp]'`. Telegram is never exposed.
  Prefer `--mcp-transport stdio` locally; SSE binds `127.0.0.1` only.
- `python main.py --task tw_stock_movers` runs the Taiwan stock-mover task
  during market hours, persists `tw_stock_mover` signals, and reports separate
  top gainers and losers for TWSE listed (上市) and TPEx OTC (上櫃) stocks.
- `python main.py --task stock_watchlist` runs personal watchlist threshold
  alerts during market hours, persists `stock_watchlist_alert` signals, and
  sends Telegram only when newly triggered alerts exist.
- `python main.py --task tw_market_assistant` builds a research-oriented Taiwan
  market digest (theme bullets, watchlist hits, ranked movers, research
  candidates) during market hours. It is research-only — never buy/sell advice.
  Enable under `tasks.tw_market_assistant` and optional `investment:` theme
  mappings; k3s ships it disabled with no CronJob until you schedule it.
- `python main.py --telegram-test` sends a one-off Telegram test message using
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Structured logging

`fetch`, `job_search`, `job_research_agent`, `tw_stock_movers`, `stock_watchlist`,
and `tw_market_assistant` emit JSON log
lines to stdout so Docker and k3s can collect task, source, and notification
events from container logs. Human-readable summaries are still printed for local
CLI use.

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

`python main.py --task job_search` runs rule-based scoring first, then optionally
refines plausible candidates with the OpenAI-backed scorer when
`scoring.llm_enabled` is true. Jobs below `scoring.llm_min_rule_score` stay
rule-only; `scoring.llm_max_jobs_per_run` caps LLM calls per run. LLM failures
fall back to the rule score without failing the task. Duplicate jobs are still
stored. Unscored duplicates from earlier `fetch` runs are scored on the next
`job_search` run, but jobs that were already reported in a digest are not
rescored or included again. Telegram digests honor
`digest_max_strong_matches`, `digest_max_maybe_matches`, and
`include_rejected_summary`.

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

`python main.py --task stock_watchlist` uses the same Taiwan market-hours /
holiday guard. It reuses TWSE/TPEx quote sources, filters to configured
`symbols`, and creates `stock_watchlist_alert` rows when
`abs(change_percent) >= alert_change_percent_abs`. Alerts dedupe by minute
bucket (`watchlist-{YYYYMMDD}-{HHMM}-{code}`). Telegram is sent only for newly
inserted alerts (same-minute re-runs stay quiet). Optional `themes` group
triggered symbols in the digest.

Example Telegram output:

```text
自選股異動快報 2026-06-25 10:20

價格異動
1. 3081 聯亞 +4.12% — 超過設定門檻 3.0%
2. 2330 台積電 -2.20% — 超過設定門檻 2.0%

主題觀察
CPO_optical: 1 檔觸發異動
AI_infrastructure: 1 檔觸發異動
```

## Taiwan market hours

`tw_stock_movers` workflows should call `is_tw_market_open()` (or
`tw_market_skip_reason()`) before fetching or sending reports so frequent
schedulers can no-op outside 台股開盤時間:

```python
from app.core import is_tw_market_open, tw_market_skip_reason

skip_reason = tw_market_skip_reason()
if skip_reason is not None:
    print(f"tw market unavailable ({skip_reason}); skipping stock mover run")
    return

# Equivalent boolean check:
if not is_tw_market_open():
    print("tw market closed; skipping stock mover run")
    return
```

The guard treats Monday through Friday, 09:00 through 13:30 inclusive, in
`Asia/Taipei`, and also skips configured Taiwan market holidays / settlement-only
no-trade days. Skip reasons:

- `taiwan_market_holiday` — date listed in the static holiday calendar
- `taiwan_market_closed` — weekend or outside market hours
- `taiwan_market_calendar_unavailable` — the holiday YAML has no entries for the
  requested year (fail closed until the calendar is updated)

### Updating the holiday calendar

Holiday data lives in `app/data/tw_market_holidays.yaml` and ships with the
package. Keep every year you plan to schedule covered: if the requested Taipei
year has zero holiday rows, stock tasks skip with
`taiwan_market_calendar_unavailable` instead of assuming the market is open.

When TWSE publishes the next year's schedule
(https://www.twse.com.tw/holidaySchedule/holidaySchedule):

1. Add each no-trade date under `holidays:` with `date` (`YYYY-MM-DD`) and
   `name`.
2. Include settlement-only days (市場無交易，僅辦理結算交割作業).
3. Do not list ordinary weekends; weekday/time checks already cover those.
4. Leave informational “開始交易日” / “最後交易日” rows out — those are trading
   days.
5. Add announced ad-hoc closures (for example typhoon / emergency no-trade days)
   when TWSE publishes them; the static file cannot discover those automatically.
6. Run `make check` and commit the YAML update.

## Storage

Signals are persisted in SQLite through `app/core/db.py`. Local databases typically
live under `data/` and are ignored by git.

`init_db()` is the single schema entrypoint used by tasks. It applies numbered
SQL migrations from `app/sql/migrations/` (packaged with the wheel) in order and
records the version with SQLite `PRAGMA user_version`. Legacy unversioned
databases created with the MVP `CREATE TABLE IF NOT EXISTS` bootstrap are
upgraded safely: missing tables/indexes are created, older `signals` shapes are
backfilled (for example a missing `content` column), and only then is the
version advanced. Do not delete `data/signals.sqlite3` just to pick up schema
changes.

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
    # Legacy single-profile shape (still supported). Omit `profiles` to use this.
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
    # Or use multiple named profiles in one job_search run:
    # profiles:
    #   - name: cloud_sdet
    #     sources: [job104, linkedin_email]
    #     keywords: [Cloud SDET, Python]
    #     locations: [Taiwan, Remote]
    #     scorer_profile: app/profiles/henry_job_profile.md
    #   - name: devsecops_security
    #     sources: [job104, linkedin_email]
    #     keywords: [DevSecOps, Cloud Security]
    #     locations: [Taiwan, Remote]
  tw_stock_movers:
    enabled: true
    top_n: 20
    timezone: Asia/Taipei
  stock_watchlist:
    enabled: false
    timezone: Asia/Taipei
    sources:
      - twse
      - tpex
    symbols:
      - code: "2330"
        name: 台積電
        alert_change_percent_abs: 2.0
      - code: "3081"
        name: 聯亞
        alert_change_percent_abs: 3.0
    themes:
      AI_infrastructure:
        symbols: ["2330"]
      CPO_optical:
        symbols: ["3081"]
  tw_market_assistant:
    enabled: false
    timezone: Asia/Taipei
    market_hours_only: true
    top_n: 20
    notify_language: zh-TW
    sources:
      - twse
      - tpex

investment:
  watchlist:
    - "2330"
    - "2454"
  theme_mappings:
    ai_server:
      label: AI伺服器
      keywords:
        - 伺服器
        - AI
        - GPU

notifiers:
  telegram:
    enabled: true
    failure_alerts_enabled: true

scoring:
  freshness_weight: 0.6
  priority_weight: 0.4
  llm_enabled: false
  # Alias kept in sync with llm.model (either may be set; both must match).
  llm_model: gpt-4o-mini
  # Optional LLM budget and digest caps when llm_enabled: true
  # llm_min_rule_score: 25
  # llm_max_jobs_per_run: 20
  # digest_max_strong_matches: 10
  # digest_max_maybe_matches: 10
  # include_rejected_summary: true

# Optional LLM gateway defaults (provider/model/timeout/temperature).
# llm.model is the source of truth for the OpenAI/fake gateway model.
llm:
  provider: openai
  model: gpt-4o-mini
  timeout_seconds: 20
  temperature: 0.2

# Optional. Omit the whole section (or any nested key) to keep safe defaults.
sources:
  job104:
    timeout_seconds: 10
    max_retries: 2
    request_delay_seconds: 1.0
    page_limit: 1
  indeed:
    timeout_seconds: 10
    max_retries: 2
    request_delay_seconds: 1.0
    page_limit: 1
  glassdoor:
    timeout_seconds: 10
    max_retries: 2
    request_delay_seconds: 1.0
    page_limit: 1
  linkedin_email:
    lookback_days: 14
    # Optional; when omitted, LINKEDIN_EMAIL_IMAP_MAILBOX is used (default INBOX).
    imap_mailbox: INBOX
  twse:
    timeout_seconds: 10
    max_retries: 2
    request_delay_seconds: 0.2
    batch_size: 100
  tpex:
    timeout_seconds: 10
    max_retries: 2
    request_delay_seconds: 0.2
    batch_size: 100
```

Load config in code with:

```python
from app.core.config import load_config, load_env_settings

config = load_config("config.yaml")
env = load_env_settings()
```

- Put local secrets in `.env`, not in `config.yaml` or committed files.
- Set `OPENAI_API_KEY` in `.env` before enabling `scoring.llm_enabled: true`
  with `llm.provider: openai` (default). The offline `llm.provider: fake`
  path does not require an OpenAI key.
- Set `llm.model` in `config.yaml` to choose which model the gateway uses for
  the optional LLM scorer. `scoring.llm_model` remains a supported alias and
  is kept in sync; setting both to different values raises `ConfigError`.
- Optional `llm:` gateway settings also control provider (`openai` / `fake`),
  timeout, and temperature.
- Optional agent extras: `pip install -e '.[langchain]'` adds `langchain-core`
  and `langgraph` for `--langchain-smoke` and the LangGraph path inside
  `job_research_agent`. `pip install -e '.[mcp]'` adds the MCP SDK for
  `--mcp-serve`. The base install stays free of those packages.
- Agent memory (`app/memory/`): configure explicit Markdown/text paths under
  `memory.namespaces.<name>.documents` in `config.yaml` (see the sample at
  `app/evaluation/fixtures/company-notes.md`). Deployment defaults leave the
  list empty so Compose/k3s images do not warn about missing files.
  `job_research_agent` ingests configured paths into the shared SQLite DB
  (migration `002_agent_memory.sql`) under
  `tasks.job_research_agent.memory_namespace` before retrieval. Keyword
  retrieval is default; embeddings are optional/fake in CI. Never ingest
  `.env`, credentials, or secrets (path and content checks). Clear a namespace
  with `SqliteMemoryStore(...).delete_namespace("job_research")`.
- Offline evaluation: `make eval` (also run in GitHub Actions CI) runs the
  `core` suite. Cases ship inside the package at
  `app/evaluation/cases/*.yaml`. Pass `--json-out` to write a machine-readable
  report and redacted traces. An empty/misspelled suite fails. Paid
  live-provider eval requires both `SIGNAL_AGENT_LIVE_EVAL=1` and
  `inputs.allow_live: true` on a case; it is never enabled in CI.
- MCP security limits: bind localhost only; expose read-oriented allowlisted
  tools; no public internet exposure, no shell/filesystem catch-alls, and no
  Telegram/side-effect tools by default.
- Two-stage scoring: rule score always runs first; LLM runs only when enabled,
  the rule score is at or above `scoring.llm_min_rule_score`, and
  `scoring.llm_max_jobs_per_run` has not been reached. Stored reasons are
  prefixed with `[rule]`, `[llm]`, or `[rule_fallback]`.
- Digest caps: `digest_max_strong_matches` and `digest_max_maybe_matches` limit
  listed jobs; set `include_rejected_summary: false` to omit the rejected summary.
- Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` before using
  `--telegram-test` or the Telegram notifier.
- `notifiers.telegram.failure_alerts_enabled` controls best-effort Telegram
  alerts when a top-level task exits with an error. If omitted, it defaults to
  the same value as `notifiers.telegram.enabled`.
- Optional top-level `sources:` tunes HTTP timeouts, retries, request delays,
  job-search `page_limit`, stock `batch_size`, and LinkedIn email
  `lookback_days` / `imap_mailbox` without code changes. Invalid values raise
  `ConfigError` at load time.
- `tasks.job_search.profiles` runs multiple named searches in one task. Each
  profile has its own `sources`, `keywords`, `locations`, and optional
  `scorer_profile` Markdown path. When `profiles` is omitted, the legacy
  top-level `sources` / `keywords` / `locations` become a single `default`
  profile. Digests group under `## profile_name` when more than one profile is
  configured; duplicate URLs still upsert to one DB row across profiles.
- For `linkedin_email`, either set `LINKEDIN_EMAIL_SOURCE_PATH` to a local
  `.eml` file or directory, or set `LINKEDIN_EMAIL_IMAP_HOST`,
  `LINKEDIN_EMAIL_IMAP_USERNAME`, and `LINKEDIN_EMAIL_IMAP_PASSWORD`. Prefer
  `sources.linkedin_email.imap_mailbox` in `config.yaml` for the mailbox; if
  that key is omitted, `LINKEDIN_EMAIL_IMAP_MAILBOX` is used (default
  `INBOX`).
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
- `make eval` (offline `core` evaluation suite; no network/API tokens)
- `make package-smoke` (build wheel, install it, verify `signal-agent --list-tasks`
  and bundled profile data)

Use `make check` locally before opening a PR; it runs all four targets in
order.

Use either the documented commands directly or the `Makefile` targets:

```bash
make install-dev
make test
make lint
make eval
make format
make check
```

Equivalent direct commands:

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest
python3 -m ruff check .
python3 -m app.evaluation --suite core
python3 -m ruff format .
```

The `Makefile` defaults to `python3`, which matches typical Ubuntu setups. To
use a different interpreter, run `make test PYTHON=python3.12`.
