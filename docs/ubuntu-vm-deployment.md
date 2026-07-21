# Ubuntu VM deployment runbook

This is the recommended first production path for `signal-agent`: a plain
Ubuntu VM running Docker Compose with host-level cron (or optional systemd
timers).

**k3s is optional.** You do not need Kubernetes for MVP. Use
`deploy/k3s/` only after this VM path is stable.

Helper scripts and a crontab example live under `deploy/ubuntu/`.

## Prerequisites

- Ubuntu 22.04+ (or similar)
- Docker Engine + Compose plugin (`docker compose version`)
- Your Linux user in the `docker` group (so cron can run without root)
- Git
- Host timezone **Asia/Taipei** when using cron (see below)

Install Docker from Docker’s official Ubuntu packages (recommended over the
distro `docker.io` package):  
https://docs.docker.com/engine/install/ubuntu/

Then:

```bash
sudo usermod -aG docker "$USER"
# log out and back in (or reboot) so the group membership applies
sudo timedatectl set-timezone Asia/Taipei
docker --version
docker compose version
timedatectl   # Time zone should be Asia/Taipei
```

**Cron timezone note (Ubuntu 22.04):** Jammy’s cron daemon uses a single
system timezone and does **not** honor per-crontab `CRON_TZ`. Set the host
with `timedatectl` as above. Optional systemd timers embed `Asia/Taipei`
directly in each `OnCalendar=` expression, so they still fire correctly even
if the host stays on UTC.

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

`.env` is **required** for Compose (`docker-compose.yml` loads it). Create it
even if some values stay empty for dry runs.

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
- Per-task lock files under `data/.*.lock` prevent overlapping Compose runs.

## 4. Build the Docker image

```bash
cd /opt/signal-agent
docker compose build
```

Rebuild after every `git pull` that changes application code or the Dockerfile.

## 5. Smoke tests

```bash
cd /opt/signal-agent

# List tasks (Telegram secrets not required for this command)
docker compose run --rm -T signal-agent python main.py --list-tasks

# Entrypoint wiring
docker compose run --rm -T signal-agent python main.py --task dummy

# Optional Telegram connectivity check (needs Telegram secrets)
docker compose run --rm -T signal-agent python main.py --telegram-test

# Manual task runs (may fail clearly if secrets/sources are missing)
docker compose run --rm -T signal-agent python main.py --task job_search
docker compose run --rm -T signal-agent python main.py --task tw_stock_movers
```

Or use the wrappers (same commands; skip cleanly if another run is in progress):

```bash
./deploy/ubuntu/run-job-search.sh
./deploy/ubuntu/run-tw-stock-movers.sh
```

## 6. Install schedules (cron — preferred)

Copy the example and install for the same user that owns `/opt/signal-agent`
and can run `docker` without sudo:

```bash
cd /opt/signal-agent
crontab -l > /tmp/crontab.bak 2>/dev/null || true
# Review deploy/ubuntu/crontab.example, then merge into your crontab:
crontab -e
```

Paste the contents of `deploy/ubuntu/crontab.example` (adjust `SIGNAL_AGENT_HOME`
if the repo is not at `/opt/signal-agent`). Confirm `timedatectl` shows
`Asia/Taipei` first — otherwise 08:05/18:05 will follow the wrong zone.

Intended schedules (Asia/Taipei):

| Task | Schedule |
|------|----------|
| `job_search` | 08:05 and 18:05 daily |
| `tw_stock_movers` | every 10 minutes, weekdays (`1-5`) |

`tw_stock_movers` still applies its own Taiwan market-hours guard, so
off-hours weekday runs exit cleanly without sending digests.

The wrappers use `flock` so a slow `job_search` (for example LinkedIn IMAP)
does not stack concurrent Compose runs on the shared SQLite file.

Verify cron is installed:

```bash
crontab -l
```

## 7. Optional: systemd timers

If you prefer systemd over cron, use the installer (it substitutes your Linux
user and repo path into the unit templates — no hardcoded `User=ubuntu`):

```bash
cd /opt/signal-agent
sudo ./deploy/ubuntu/install-systemd.sh
# Or explicitly:
# sudo DEPLOY_USER="$USER" SIGNAL_AGENT_HOME=/opt/signal-agent \
#   ./deploy/ubuntu/install-systemd.sh
systemctl list-timers 'signal-agent-*'
```

The installer is safe to re-run after `git pull`. Timer `OnCalendar=` lines
include `Asia/Taipei` so schedules do not depend on the host timezone.

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
docker compose run --rm -T signal-agent python main.py --task job_search
docker compose run --rm -T signal-agent python main.py --task tw_stock_movers

# After code changes
git pull
docker compose build

# If a compose run left a stuck container
docker compose ps -a
docker compose rm -f signal-agent
```

SQLite recovery tips:

- Stop scheduled runs before copying `data/signals.sqlite3`.
- Keep occasional backups: `cp data/signals.sqlite3 "data/signals.sqlite3.bak.$(date +%F)"`.

## Troubleshooting

| Symptom | Likely fix |
|---------|------------|
| `permission denied` talking to Docker | Add user to `docker` group; re-login |
| `env_file ... must be a string` on very old Compose | Reinstall Docker Engine from Docker’s docs (or upgrade Compose) |
| `.env is missing` from wrapper | `cp .env.example .env` |
| Cron runs but `docker: command not found` | Ensure crontab `PATH` includes `/usr/bin` (see example) |
| `skipping … another run is already in progress` | Expected under lock; wait or inspect hung containers |

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
