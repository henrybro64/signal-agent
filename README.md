# signal-agent

Personal Python agent for scheduled workflows that collect, score, persist, and
notify on signals — primarily **job-search digests** and **Taiwan stock mover**
reports via Telegram.

MVP through M7 are on `main`. M8 adds an OpenClaw-like local product CLI
(`setup`, runtime lifecycle, `chat`, schedules, skills, optional browser /
systemd helpers). Coding agents: see [`AGENTS.md`](AGENTS.md).

## 5-minute quick start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

signal-agent setup --non-interactive --provider fake
signal-agent doctor
signal-agent run dummy
```

Add Telegram credentials to `.env`, then enable the notifier (non-interactive
setup defaults to `notifiers.telegram.enabled: false`):

```bash
signal-agent setup --non-interactive --provider fake --telegram-enabled
python main.py --telegram-test
signal-agent run job_search
```

Full walkthrough (BotFather, chat ID, first digest):
[`docs/getting-started.md`](docs/getting-started.md).

## Common commands

```bash
signal-agent tasks
signal-agent run tw_stock_movers
signal-agent start && signal-agent status && signal-agent stop
signal-agent schedules
signal-agent chat
signal-agent service install --no-enable
```

Legacy flags still work: `python main.py --list-tasks`, `--task …`, `--health`.

## Documentation

| Doc | Audience |
|-----|----------|
| [`docs/index.md`](docs/index.md) | Map of all docs |
| [`docs/getting-started.md`](docs/getting-started.md) | First install → Telegram |
| [`docs/configuration.md`](docs/configuration.md) | `config.yaml` + `.env` reference |
| [`docs/job-search.md`](docs/job-search.md) | Sources, profiles, scoring |
| [`docs/tw-stocks.md`](docs/tw-stocks.md) | Movers, watchlist, holidays |
| [`docs/scheduling.md`](docs/scheduling.md) | Schedules + local runtime |
| [`docs/deployment.md`](docs/deployment.md) | Local / systemd / Compose / k3s |
| [`docs/ubuntu-vm-deployment.md`](docs/ubuntu-vm-deployment.md) | Ubuntu VM runbook |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Symptom → fix |
| [`docs/development.md`](docs/development.md) | Architecture, tests, contributing |
| [`docs/vendor-neutral-ai.md`](docs/vendor-neutral-ai.md) | LLM / search / runtimes |
| [`docs/roadmap/m7-agent-platform-roadmap.md`](docs/roadmap/m7-agent-platform-roadmap.md) | Completed M7 plan |
| [`AGENTS.md`](AGENTS.md) | Coding-agent implementation map |

## Status snapshot

- Job collectors: `job104`, `linkedin_email`, best-effort `indeed` / `glassdoor`
  (anti-bot blocks possible; failures are isolated per source).
- TW stock tasks fail closed when the holiday calendar lacks the current year —
  `doctor` warns before that happens.
- Runtime logs rotate by size; idle ticks are quiet (`DEBUG`).
- **k3s is optional** — start with local runtime or the Ubuntu VM Compose path.

## License / contributing

See [`docs/development.md`](docs/development.md). Keep PRs narrow (one issue), run
`make check` locally (CI / GitHub Actions mirrors that), and never commit secrets
or `data/*.sqlite3`.
