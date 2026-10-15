# Documentation index

Operator and developer docs for `signal-agent`, grouped by audience.

## New operators

1. [Getting started](getting-started.md) — install, setup wizard, Telegram bot, first digest
2. [Configuration](configuration.md) — `config.yaml` + `.env` reference
3. [Troubleshooting](troubleshooting.md) — common failures and fixes

## Feature guides

- [Job search](job-search.md) — sources, profiles, scoring, digests
- [Taiwan stocks](tw-stocks.md) — movers, watchlist, market hours, holiday calendar
- [Scheduling](scheduling.md) — schedules CLI, cron syntax, runtime start/stop/logs

## Deployment

- [Deployment options](deployment.md) — which path to choose
- [Ubuntu VM runbook](ubuntu-vm-deployment.md) — Compose + cron/systemd timers
- [Vendor-neutral AI](vendor-neutral-ai.md) — LLM / search / coding-runtime adapters

## Developers

- [Development](development.md) — architecture, tests, evals, contributing
- [AGENTS.md](../AGENTS.md) — coding-agent project map (module boundaries, PR rules)
- [M7 roadmap](roadmap/m7-agent-platform-roadmap.md) — completed LangChain-ready platform plan

## Deploy manifests

- `deploy/ubuntu/` — cron wrappers, systemd timer examples, optional logrotate snippet
- `deploy/k3s/` — optional Cluster CronJobs + PVC-backed SQLite
