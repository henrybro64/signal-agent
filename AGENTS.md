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
| M6 | Hardening / extensions #49–#57 | **Open** — implement next |
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
│   ├── core/
│   │   ├── models.py       # SignalItem and shared models
│   │   ├── config.py       # config.yaml loader
│   │   ├── db.py           # SQLite storage, dedup, run history
│   │   ├── secrets.py      # env-based secrets
│   │   ├── time_utils.py   # Taiwan market-hours helpers
│   │   ├── logging.py      # structured JSON logging
│   │   └── error_handling.py  # Telegram failure alerts + redaction
│   ├── sources/            # BaseSource plugins → SignalItem
│   ├── tasks/              # BaseTask workflows + CLI registry
│   ├── scorers/            # rule + optional LLM job scoring
│   ├── profiles/           # packaged job-scoring profiles (*.md)
│   └── notifiers/          # Telegram delivery
├── sql/schema.sql
├── deploy/k3s/             # CronJob manifests
├── data/                   # local SQLite (gitignored)
└── tests/
```

There is **no** shared core pipeline module. Orchestration lives in tasks
(`app/tasks/*.py`) and shared helpers under `app/core/` and `app/sources/`.

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
2. **Docker Compose** — Ubuntu VM-friendly one-off and scheduled host timers; see README.
3. **k3s** — manifests under `deploy/k3s/` for CronJobs sharing a PVC-backed SQLite volume.

Do not remove earlier stages when adding a later one.

## Completed MVP map (#1–#25)

Historical order (already merged). Use as archaeology only — do not reopen unless
a regression is found.

| # | Topic | Primary areas |
|---|--------|----------------|
| 1–2 | Repo + tooling | layout, `pyproject.toml`, Makefile, ruff/pytest |
| 3–5 | Models, sources, tasks/CLI | `app/core/models.py`, `app/sources/base.py`, `app/tasks/`, `app/cli.py` |
| 6–7 | Config + secrets | `app/core/config.py`, `app/core/secrets.py`, `.env.example` |
| 8–9 | SQLite + Telegram | `app/core/db.py`, `sql/schema.sql`, `app/notifiers/telegram.py` |
| 10–15 | Job search MVP | profiles, scorers, `job104`, `linkedin_email`, `job_search` task, LLM scorer |
| 16–19 | TW stock movers | `time_utils`, `twse`, `tpex`, `tw_stock_movers` task |
| 20–22 | Packaging / deploy | `Dockerfile`, `docker-compose.yml`, `deploy/k3s/` |
| 23–25 | Ops hardening | `logging.py`, `task_runs` history, `error_handling.py` failure alerts |

## Next execution order (M6)

Work in this order unless a human asks otherwise:

```text
1. #56  Ubuntu VM deployment runbook (cron / systemd + Compose)
2. #54  GitHub Actions CI for pytest, ruff, package smoke
        Note: `.github/workflows/ci.yml` already exists — verify acceptance
        criteria and close or extend; do not duplicate a second workflow.
3. #50  Health/status CLI for scheduled runs
4. #53  Configurable source timeouts, retries, and fetch limits
5. #49  Multiple job-search profiles and per-profile configuration
6. #55  Two-stage job scoring with LLM budget and digest limits
7. #51  Taiwan market holiday calendar in market-hours guard
8. #52  Stock watchlist alert task (separate from top movers)
9. #57  SQLite schema versioning and migration path
```

Also open (schedule separately; not part of the M6 hardening sequence above):

- #29 — Taiwan stock investment assistant digest (product feature)
- #60–#67 — M7 agent-platform evolution (LangChain/LangGraph/MCP). Implement only
  when explicitly requested; preserve the current CLI and scheduled task model.

## File-level direction for open M6 issues

Use each issue body as the source of truth for acceptance criteria. Starting
points:

| Issue | Likely touch points |
|-------|---------------------|
| #56 | `README.md`, optional `docs/ubuntu-vm-runbook.md`, Compose/cron examples |
| #54 | `.github/workflows/ci.yml`, Makefile targets (extend if gaps remain) |
| #50 | `app/cli.py`, `app/core/db.py`, new status helper + tests |
| #53 | `config.yaml`, source constructors, `app/core/config.py`, source tests |
| #49 | `config.yaml`, `app/profiles/`, `app/tasks/job_search.py`, scorers |
| #55 | `app/scorers/`, `app/tasks/job_search.py`, scoring config keys |
| #51 | `app/core/time_utils.py`, holiday data + tests |
| #52 | new source/task under `app/sources/` / `app/tasks/`, config + Telegram |
| #57 | `app/core/db.py`, `sql/`, migration runner + tests |

## How to pick up work

1. Read the target GitHub issue and this guide.
2. Branch from up-to-date `main` as `issue-N-short-slug`.
3. Implement narrowly; add/adjust tests; run `make check`.
4. Update README/config/examples when behavior changes; update this guide if the
   map or order changes.
5. Open a PR that references the issue.
