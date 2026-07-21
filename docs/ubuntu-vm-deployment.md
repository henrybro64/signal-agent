# Ubuntu VM deployment runbook

This is the recommended first production path for `signal-agent`: a plain
Ubuntu VM running Docker Compose with host-level cron (or optional systemd
timers).

**k3s is optional.** You do not need Kubernetes for MVP. Use
`deploy/k3s/` only after this VM path is stable.

Helper scripts and a crontab example live under `deploy/ubuntu/`.

## Prerequisites

- Ubuntu 22.04+ (or similar)
- Docker Engine + Docker Compose plugin (`docker compose version`)
- Git
- Ability to set the host timezone or use `CRON_TZ=Asia/Taipei`

Install Docker on Ubuntu if needed (official docs):  
https://docs.docker.com/engine/install/ubuntu/

Confirm:

```bash
docker --version
docker compose version
timedatectl   # prefer Asia/Taipei, or rely on CRON_TZ below
```

## 1. Clone the repo

```bash
sudo mkdir -p /opt
sudo chown "$USER":"$USER" /opt
cd /opt
git clone https://github.com/henrybro64/signal-agent.git
cd /opt/signal-agent
```

Later updates:

```bash
cd /opt/signal-agent
git pull
docker compose build
```

## 2. Create `.env`

```bash
cd /opt/signal-agent
cp .env.example .env
chmod 600 .env
```

Edit `.env` and fill secrets you need:

- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` for digests and failure alerts
- `OPENAI_API_KEY` only if LLM scoring is enabled in `config.yaml`
- LinkedIn IMAP vars only if `linkedin_email` is enabled

Never commit `.env`.

## 3. Create persistent `data/` and `logs/`

```bash
cd /opt/signal-agent
mkdir -p data logs
```

- SQLite persists in `./data` (mounted at `/app/data` in the container).
- Cron wrappers append to `./logs/*.log`.

## 4. Build the Docker image

```bash
cd /opt/signal-agent
docker compose build
```

Rebuild after every `git pull` that changes application code or the Dockerfile.

## 5. Smoke tests

```bash
cd /opt/signal-agent

# List tasks (no secrets required)
docker compose run --rm signal-agent python main.py --list-tasks

# Entrypoint wiring
docker compose run --rm signal-agent python main.py --task dummy

# Optional Telegram connectivity check (needs Telegram secrets)
docker compose run --rm signal-agent python main.py --telegram-test

# Manual task runs (may fail clearly if secrets/sources are missing)
docker compose run --rm signal-agent python main.py --task job_search
docker compose run --rm signal-agent python main.py --task tw_stock_movers
```

Or use the wrappers (same commands, with logging-friendly exit codes):

```bash
./deploy/ubuntu/run-job-search.sh
./deploy/ubuntu/run-tw-stock-movers.sh
```

## 6. Install schedules (cron — preferred)

Copy the example and install for your user (or root if the repo is only
writable by root):

```bash
cd /opt/signal-agent
crontab -l > /tmp/crontab.bak 2>/dev/null || true
# Review deploy/ubuntu/crontab.example, then merge into your crontab:
crontab -e
```

Paste the contents of `deploy/ubuntu/crontab.example` (adjust `SIGNAL_AGENT_HOME`
if the repo is not at `/opt/signal-agent`).

Intended schedules (Asia/Taipei):

| Task | Schedule |
|------|----------|
| `job_search` | 08:05 and 18:05 daily |
| `tw_stock_movers` | every 10 minutes, weekdays (`1-5`) |

`tw_stock_movers` still applies its own Taiwan market-hours guard, so
off-hours weekday runs exit cleanly without sending digests.

Verify cron is installed:

```bash
crontab -l
```

## 7. Optional: systemd timers

If you prefer systemd over cron, example units are in
`deploy/ubuntu/systemd/`. Install:

```bash
cd /opt/signal-agent
# Edit WorkingDirectory / paths in the unit files if needed
sudo cp deploy/ubuntu/systemd/*.service /etc/systemd/system/
sudo cp deploy/ubuntu/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now signal-agent-job-search.timer
sudo systemctl enable --now signal-agent-tw-stock-movers.timer
systemctl list-timers 'signal-agent-*'
```

## 8. Inspect logs

```bash
cd /opt/signal-agent
tail -n 100 logs/job_search.log
tail -n 100 logs/tw_stock_movers.log

# Follow live
tail -f logs/tw_stock_movers.log
```

Structured application logs also go to container stdout/stderr, which the
wrappers capture into those files.

## 9. Manual run / recovery

```bash
cd /opt/signal-agent

# One-off task
./deploy/ubuntu/run-job-search.sh
./deploy/ubuntu/run-tw-stock-movers.sh

# Or without wrappers
docker compose run --rm signal-agent python main.py --task job_search
docker compose run --rm signal-agent python main.py --task tw_stock_movers

# After code changes
git pull
docker compose build

# If a compose run left a stuck container
docker compose ps -a
docker compose rm -f
```

SQLite recovery tips:

- Stop scheduled runs before copying `data/signals.sqlite3`.
- Keep occasional backups: `cp data/signals.sqlite3 "data/signals.sqlite3.bak.$(date +%F)"`.

## Path comparison

| Path | When to use |
|------|-------------|
| **Ubuntu VM + Compose + cron/systemd** | Default MVP / personal deploy |
| Local Python (`pip install -e`) | Development |
| k3s CronJobs (`deploy/k3s/`) | Optional later; not required for MVP |

## Security notes

- Keep `.env` mode `600` and out of Git.
- Prefer a dedicated Linux user owning `/opt/signal-agent`.
- Do not expose Telegram or email credentials in crontab lines; the wrappers
  load `.env` through Docker Compose.
