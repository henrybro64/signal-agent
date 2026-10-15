# Troubleshooting

## Source blocked or rate-limited

**Symptoms:** `job104` / `indeed` / `glassdoor` failures in logs; empty digests;
`doctor` still green.

**Cause:** Provider anti-bot challenges, HTTP 403/429, or network blocks.

**Fix:** Prefer `linkedin_email` + `job104` with conservative
`sources.*.request_delay_seconds` / `page_limit`. Failed sources are isolated —
check `signal_runs` / task digests for per-source errors. Do not scrape LinkedIn
while logged in; use job-alert email.

## Missing secrets

**Symptoms:** `doctor` errors; Telegram test fails; LLM stays on rules.

**Fix:**

```bash
signal-agent doctor
# Ensure .env has the vars from .env.example you actually need
python main.py --telegram-test
```

Missing LLM keys fail open to rule scoring for `job_search`. Missing Telegram
tokens block digests and failure alerts.

## Market closed / holiday / calendar skips

**Symptoms:** `tw_stock_movers` prints a skip label and exits without a digest.

| Label / reason | Meaning |
|----------------|---------|
| Taiwan market is closed | Weekend or outside 09:00–13:30 |
| Taiwan market holiday | Date in `app/data/tw_market_holidays.yaml` |
| Taiwan market calendar unavailable | No holiday rows for that year |

**Fix:** Run during session, or update the holiday YAML when TWSE publishes the
next year. `doctor` warns when coverage is about to lapse. Look for structured
`calendar_unavailable` log events (distinct from ordinary closed skips).

## Runtime will not start or stop

**Symptoms:** `Runtime already running`, `stale pid file`, or `stop` reports
stopped while the process lives.

**Fix:**

```bash
signal-agent status
signal-agent stop
# If needed: ensure only one supervisor (runtime XOR systemd --user)
signal-agent service status
```

Ownership checks use full `ps -ww` / `/proc/.../cmdline` so long venv paths are
not truncated. Logs: `signal-agent logs --lines 100`.

## Schedule never runs

**Symptoms:** `schedules` shows `next=-`, or `last=running` forever.

**Causes / fixes:**

- Invalid cron → `schedule add` should reject; delete and re-add with a valid
  expression (DOW `7` is Sunday; DOM+DOW uses OR).
- Runtime not started → `signal-agent start` or enable the user service.
- Stuck `running` after a kill → restart the runtime (startup reclaims orphans).
- Permission policy blocked the task → check `tools.permissions` and schedule
  history.

## Doctor warnings

Treat `warning` severity as actionable before production scheduling. Info lines
(browser detection, calendar coverage, skills) are expected on healthy installs.
