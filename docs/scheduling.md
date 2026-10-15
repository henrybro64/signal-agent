# Scheduling and local runtime

## Persistent schedules

```bash
signal-agent schedules
signal-agent schedule add --name movers --task tw_stock_movers \
  --timezone Asia/Taipei --cron 'every:30'
signal-agent schedule add --name jobs --task job_search \
  --timezone Asia/Taipei --cron '0 20 * * 1-5'
signal-agent schedule enable <id>
signal-agent schedule disable <id>
signal-agent schedule run <id>
signal-agent schedule history <id>
```

### Cron syntax

Supported forms:

- `every:<minutes>` — interval from “now” (example: `every:15`)
- 5-field cron: `min hour dom month dow`
  - `*`, `*/N`, lists (`1,2,5`), ranges (`1-5`), stepped ranges (`1-5/2`)
  - DOW `0`–`7` (`0` and `7` = Sunday)
  - When both day-of-month and day-of-week are restricted, matching uses
    standard cron **OR** semantics
- Invalid / never-matching expressions are rejected at `schedule add` time

Timezone is required and must be an IANA name (for example `Asia/Taipei`).

## Local runtime

```bash
signal-agent start
signal-agent status
signal-agent logs --lines 80
signal-agent stop
signal-agent restart
```

The runtime holds an exclusive flock, runs worker ticks (health probe + due
schedules), and writes JSON to `logs/signal-agent.log` with one in-process
rotator (defaults 5 MiB × 5 backups; override with `SIGNAL_AGENT_LOG_MAX_BYTES`
/ `SIGNAL_AGENT_LOG_BACKUP_COUNT`). Idle successful ticks log at DEBUG and are
dropped at the default INFO level.

Optional host logrotate for **cron wrapper** logs (`job_search.log`, …):
`deploy/ubuntu/logrotate-signal-agent.conf`. Do not point it at the runtime
JSON file.

## systemd --user

```bash
signal-agent service install --no-enable
signal-agent service status
# when ready:
signal-agent service install   # enable via systemctl --user
```

`ExecStart` keeps the venv interpreter path (does not resolve the symlink away).
See [Deployment](deployment.md) for when to prefer this over Compose timers.
