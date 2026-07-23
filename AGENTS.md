# signal-agent — Codex / agent implementation guide

This file is the durable project map for coding agents (Codex, Cursor, humans
implementing GitHub issues). Prefer this guide over historical issue text when
the two disagree about current layout or status.

GitHub issue #26 tracks the creation of this guide. Do **not** treat #26 as a
request to reimplement closed MVP work (#1–#25).

## Project intent

`signal-agent` is a lightweight Python personal agent for scheduled workflows
that collect, score, persist, and notify on signals. Primary MVP workflows:

1. Job-search digests from configured sources (104, LinkedIn job-alert email,
   and optional best-effort web sources).
2. Taiwan stock mover digests (台股前20漲跌幅) during market hours.

The framework should stay generic enough for later tasks (news, career pages,
watchlists, investment assistants) without collapsing module boundaries.

## Current status

| Milestone | Scope | Status |
|-----------|--------|--------|
| M0–M5 | Issues #1–#25 (bootstrap through failure alerts) | **Complete** on `main` |
| M6 | Hardening / extensions #49–#57 | **In progress** — through #52 done; #57 in progress |
| M7 | LangChain-ready platform evolution #60–#67 | Later — only when asked |

Deployment path already exists: local Python → Docker Compose → k3s CronJobs.

## High-level architecture (current)

```text
signal-agent/
├── main.py                 # thin CLI entry
├── app/cli.py              # argparse + task runner + failure-alert wrap
├── config.yaml             # non-secret runtime settings
├── .env.example            # secret names only
├── Dockerfile
├── docker-compose.yml
├── AGENTS.md               # this guide
├── README.md               # human operator docs
├── app/
│   ├── config.py           # backward-compatible re-exports (prefer app.core.*)
│   ├── core/
│   │   ├── models.py       # SignalItem and shared models
│   │   ├── config.py       # config.yaml loader
│   │   ├── db.py           # SQLite storage, dedup, run history
│   │   ├── migrations.py   # SQLite schema versioning (PRAGMA user_version)
│   │   ├── secrets.py      # env-based secrets
│   │   ├── time_utils.py   # Taiwan market-hours helpers
│   │   ├── tw_market_calendar.py  # static TW holiday calendar
│   │   ├── logging.py      # structured JSON logging
│   │   └── error_handling.py  # Telegram failure alerts + redaction
│   ├── data/
│   │   └── tw_market_holidays.yaml  # TWSE/TPEx no-trade dates
│   ├── sources/
│   │   ├── base.py         # BaseSource + fetch_from_sources helper
│   │   ├── http_settings.py  # shared HTTP timeout/retry/delay validation
│   │   ├── dummy.py        # in-memory sample source
│   │   ├── job104.py       # 104 job search
│   │   ├── linkedin_email.py  # LinkedIn job-alert email parsing
│   │   ├── indeed.py       # best-effort Indeed collector
│   │   ├── glassdoor.py    # best-effort Glassdoor collector
│   │   ├── twse.py         # TWSE movers
│   │   └── tpex.py         # TPEx movers
│   ├── tasks/
│   │   ├── base.py         # BaseTask
│   │   ├── dummy.py        # CLI wiring smoke task
│   │   ├── fetch.py        # multi-source fetch + persist bootstrap
│   │   ├── job_search.py   # job digest workflow
│   │   ├── tw_stock_movers.py  # TW stock mover digest workflow
│   │   ├── stock_watchlist.py  # personal watchlist threshold alerts
│   │   └── market_task_support.py  # shared TW market task helpers
│   ├── scorers/
│   │   ├── job_rule_scorer.py
│   │   ├── job_llm_scorer.py   # optional; fail open to rules
│   │   ├── job_score_pipeline.py  # two-stage rule → LLM orchestration
│   │   └── job_search.py   # profile loading + score orchestration
│   ├── profiles/           # packaged job-scoring profiles (*.md)
│   └── notifiers/
│       └── telegram.py
├── sql/
│   ├── schema.sql          # docs pointer to migrations (no duplicated DDL)
│   └── migrations/         # numbered SQLite migrations (NNN_name.sql)
├── docs/
│   └── ubuntu-vm-deployment.md  # Ubuntu VM + Compose + cron/systemd runbook
├── deploy/
│   ├── ubuntu/             # cron wrappers, crontab.example, optional systemd units
│   └── k3s/                # CronJob manifests (optional)
├── data/                   # local SQLite (gitignored)
└── tests/
```

There is **no** shared `app/core/pipeline.py` module (historical issue #26 sketched
one; it was never shipped). Orchestration lives in tasks (`app/tasks/*.py`) and
the shared `fetch_from_sources()` helper in `app/sources/base.py`.

## Module boundaries

| Layer | Responsibility | Do not |
|-------|----------------|--------|
| `core` | Models, config, DB, secrets, time, logging, error alerts | Provider HTTP/scrape logic |
| `sources` | One provider → `list[SignalItem]` | Scoring, Telegram, CLI |
| `tasks` | One end-to-end workflow | Reimplement storage/notifier internals |
| `scorers` | Ranking / filtering | Fetching remote data |
| `notifiers` | Outbound delivery | Business ranking rules |
| `profiles` | Packaged preference text for scorers | Secrets |

## Coding rules

- Prefer Python-only implementation unless an issue explicitly requires otherwise.
- Do not add a frontend.
- Keep secrets out of Git. Use `.env` locally, `.env.example` in Git, and
  `deploy/k3s/secret.example.yaml` for cluster secrets.
- Use SQLite first. Do not introduce Postgres unless requested.
- Evolve SQLite via numbered files under `sql/migrations/` and
  `app/core/migrations.py` (`PRAGMA user_version`). Keep `init_db()` as the
  single entrypoint; do not introduce Alembic unless explicitly requested.
- Rule-based scoring is the default path; LLM scoring must remain optional and
  fail open to rules.
- Do not automate applying to jobs or messaging recruiters.
- For LinkedIn, prefer job-alert email parsing over scraping/login automation.
- Taiwan stock digests use Traditional Chinese.
- Add tests for pure logic before live network calls.
- External collectors must fail gracefully and not abort unrelated sources/tasks.
- Failure alerts and logging must never mask the original task exception or leak
  secrets.
- Keep PRs narrow: one issue (or a tightly related pair) per PR.

## PR expectations

Each PR should:

- Reference the GitHub issue it solves (`Closes #N` when accepting the issue).
- Include tests where reasonable (pure logic, CLI wiring, manifest/config
  contracts).
- Update `README.md` / `config.yaml` / `.env.example` when operator-facing
  behavior changes.
- Update this `AGENTS.md` when architecture, module layout, or recommended
  execution order changes.
- Avoid committing real secrets, local databases, or runtime logs.

Local verification before push:

```bash
make check
```

CI (`.github/workflows/ci.yml`) already runs `make test`, `make lint`, and
`make package-smoke` on push and pull request.

## Deployment evolution

1. **Local** — `python -m venv .venv`, `pip install -e '.[dev]'`, `python main.py --task …`
2. **Docker Compose** — Ubuntu VM-friendly one-off and scheduled host timers;
   see `docs/ubuntu-vm-deployment.md` and `deploy/ubuntu/`.
3. **k3s** — manifests under `deploy/k3s/` for CronJobs sharing a PVC-backed SQLite volume.
   Optional; not required for MVP.

Do not remove earlier stages when adding a later one.

## Completed MVP map (#1–#25)

Historical order (already merged). Use as archaeology only — do not reopen unless
a regression is found. Paths below are the current landing spots for each slice.

| # | Topic | Primary files / areas |
|---|--------|------------------------|
| 1–2 | Repo + tooling | `pyproject.toml`, `Makefile`, `main.py`, ruff/pytest |
| 3 | `SignalItem` model | `app/core/models.py` |
| 4 | Source plugin interface | `app/sources/base.py`, `app/sources/dummy.py` |
| 5 | Task interface + CLI | `app/tasks/base.py`, `app/tasks/dummy.py`, `app/cli.py` |
| 6–7 | Config + secrets | `app/core/config.py`, `app/core/secrets.py`, `.env.example`, `deploy/k3s/secret.example.yaml` |
| 8 | SQLite + dedup | `app/core/db.py`, `sql/schema.sql` |
| 9 | Telegram notifier | `app/notifiers/telegram.py` |
| 10–11 | Job profile + rule scorer | `app/profiles/`, `app/scorers/job_rule_scorer.py`, `app/scorers/job_search.py` |
| 12–13 | Job sources | `app/sources/job104.py`, `app/sources/linkedin_email.py`, `app/sources/indeed.py`, `app/sources/glassdoor.py` |
| 14–15 | Job-search task + optional LLM | `app/tasks/job_search.py`, `app/scorers/job_llm_scorer.py` |
| 16 | Market-hours guard | `app/core/time_utils.py` (+ holiday calendar in #51) |
| 17–19 | TW stock movers | `app/sources/twse.py`, `app/sources/tpex.py`, `app/tasks/tw_stock_movers.py` |
| 20–22 | Packaging / deploy | `Dockerfile`, `docker-compose.yml`, `deploy/k3s/` |
| 23–25 | Ops hardening | `app/core/logging.py`, `task_runs` in `app/core/db.py`, `app/core/error_handling.py` |

Also present from MVP bootstrap (not a separate open issue): `app/tasks/fetch.py`
multi-source fetch/persist helper used for wiring and source-level failure isolation.
## Next execution order (M6)

Work in this order unless a human asks otherwise:

```text
1. #56  Ubuntu VM deployment runbook (cron / systemd + Compose) — DONE
2. #54  GitHub Actions CI for pytest, ruff, package smoke — DONE
3. #50  Health/status CLI for scheduled runs — DONE
4. #53  Configurable source timeouts, retries, and fetch limits — DONE
5. #49  Multiple job-search profiles and per-profile configuration — DONE
6. #55  Two-stage job scoring with LLM budget and digest limits — DONE
7. #51  Taiwan market holiday calendar in market-hours guard — DONE
8. #52  Stock watchlist alert task (separate from top movers) — DONE
9. #57  SQLite schema versioning and migration path
```

Also open (schedule separately; not part of the M6 hardening sequence above):

- #29 — Taiwan stock investment assistant digest (product feature). Parked behind
  M6; prefer completing #52 first so the epic can reuse watchlist alerts.
- #60–#67 — M7 agent-platform evolution (LangChain/LangGraph/MCP). Implement only
  when explicitly requested; preserve the current CLI and scheduled task model.

## File-level direction for open M6 issues

Use each issue body as the source of truth for acceptance criteria. Starting
points:

| Issue | Likely touch points |
|-------|---------------------|
| #56 | `docs/ubuntu-vm-deployment.md`, `deploy/ubuntu/` (wrappers, crontab, `install-systemd.sh`) — done |
| #54 | `.github/workflows/ci.yml`, `tests/test_ci_workflow.py`, README CI notes — done |
| #50 | `app/cli.py`, `app/core/db.py`, `app/core/health.py`, tests — done |
| #53 | `config.yaml` `sources:`, `app/core/config.py`, source `from_config`, tests — done |
| #49 | `config.yaml`, `app/core/config.py`, `app/tasks/job_search.py`, source builder, tests — done |
| #55 | `app/scorers/job_score_pipeline.py`, `app/tasks/job_search.py`, scoring config keys — done |
| #51 | `app/core/tw_market_calendar.py`, `app/data/tw_market_holidays.yaml`, `app/core/time_utils.py`, tests — done |
| #52 | `app/tasks/stock_watchlist.py`, `app/tasks/market_task_support.py`, config + Telegram — done |
| #57 | `app/core/migrations.py`, `app/core/db.py` `init_db()`, `sql/migrations/`, tests — in this PR |

## How to pick up work

1. Read the target GitHub issue and this guide.
2. Branch from up-to-date `main` as `issue-N-short-slug`.
3. Implement narrowly; add/adjust tests; run `make check`.
4. Update README/config/examples when behavior changes; update this guide if the
   map or order changes.
5. Open a PR that references the issue.
