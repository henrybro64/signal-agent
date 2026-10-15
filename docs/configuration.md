# Configuration reference

Non-secret settings live in `config.yaml`. Secrets live in `.env` (see
`.env.example`). Never put API keys or Telegram tokens in Git.

```bash
signal-agent config show
signal-agent doctor
```

Keys omitted from YAML take the defaults below. Nested maps such as
`tasks.job_search.profiles` replace the implicit single `default` profile when
present.

MCP (`.[mcp]`) and the offline eval runner (`make eval`) are **not**
`config.yaml` keys; they use CLI flags / env (`SIGNAL_AGENT_LIVE_EVAL`).

## Secrets and process env

| Variable | Required for | Default / notes |
|----------|--------------|-----------------|
| `OPENAI_API_KEY` | `llm.provider: openai` | Also accepted via `providers.openai.api_key_env` |
| `ANTHROPIC_API_KEY` | `llm.provider: anthropic` | |
| `PERPLEXITY_API_KEY` | `search.provider: perplexity` | |
| `CODEX_API_KEY` | `runtime.default: codex` | |
| `TELEGRAM_BOT_TOKEN` | Digests / failure alerts | From BotFather |
| `TELEGRAM_CHAT_ID` | Digests / failure alerts | User or group id |
| `LINKEDIN_EMAIL_SOURCE_PATH` | Local `.eml` LinkedIn ingest | File or directory |
| `LINKEDIN_EMAIL_IMAP_HOST` | IMAP LinkedIn ingest | |
| `LINKEDIN_EMAIL_IMAP_USERNAME` | IMAP LinkedIn ingest | |
| `LINKEDIN_EMAIL_IMAP_PASSWORD` | IMAP LinkedIn ingest | App password preferred |
| `LINKEDIN_EMAIL_IMAP_MAILBOX` | IMAP mailbox fallback | Default `INBOX` if config omits mailbox |
| `SIGNAL_AGENT_ENV` | Label only | `development` |
| `SIGNAL_AGENT_DB_PATH` | SQLite path override | Default `data/signal-agent.db` |
| `SIGNAL_AGENT_LOG_PATH` | Runtime JSON log path | Default `logs/signal-agent.log` |
| `SIGNAL_AGENT_RUNTIME_DIR` | Runtime state directory | Default project root |
| `SIGNAL_AGENT_LOG_MAX_BYTES` | In-process log rotation | `5242880` (5 MiB); integer ≥ 1024 |
| `SIGNAL_AGENT_LOG_BACKUP_COUNT` | Rotated backups to keep | `5`; integer ≥ 0 |
| `SIGNAL_AGENT_BROWSER_PATH` | Chromium override | Unset → auto-detect |
| `SIGNAL_AGENT_BROWSER_BACKEND` | Browser backend | Unset → Chromium; `fake` for fixtures |
| `SIGNAL_AGENT_SYSTEMD_USER_DIR` | systemd `--user` unit dir | Unset → XDG user unit path |
| `SIGNAL_AGENT_LIVE_EVAL` | Paid live eval | Unset; `1` required with `allow_live` |

## `config.yaml` defaults

### Agent

| Key | Default | Description |
|-----|---------|-------------|
| `agent.timezone` | `Asia/Taipei` | Agent-facing timezone |
| `agent.digest_time` | `20:00` | Informational digest preference |

### Tasks — job search

| Key | Default | Description |
|-----|---------|-------------|
| `tasks.job_search.enabled` | `true` | Enable job-search task |
| `tasks.job_search.sources` | `job104`, `linkedin_email` | Collectors for the implicit `default` profile |
| `tasks.job_search.keywords` | (list) | Keyword filters |
| `tasks.job_search.locations` | (list) | Location filters |
| `tasks.job_search.profiles` | omitted → one `default` | Named profiles; each has `name`, `sources`, `keywords`, `locations`, optional `scorer_profile` |

### Tasks — Taiwan stocks

| Key | Default | Description |
|-----|---------|-------------|
| `tasks.tw_stock_movers.enabled` | `true` | Top movers digest |
| `tasks.tw_stock_movers.top_n` | `20` | Movers per exchange |
| `tasks.tw_stock_movers.timezone` | `Asia/Taipei` | Market timezone |
| `tasks.stock_watchlist.enabled` | `false` | Personal threshold alerts |
| `tasks.stock_watchlist.timezone` | `Asia/Taipei` | Market timezone |
| `tasks.stock_watchlist.sources` | `[]` | Typically `twse`, `tpex` |
| `tasks.stock_watchlist.symbols` | `[]` | List of `{code, name, alert_change_percent_abs}` |
| `tasks.stock_watchlist.symbols[].code` | (required) | Ticker, e.g. `2330` |
| `tasks.stock_watchlist.symbols[].name` | `""` | Display name |
| `tasks.stock_watchlist.symbols[].alert_change_percent_abs` | `3.0` | Absolute % move that alerts |
| `tasks.stock_watchlist.themes` | `{}` | Map of theme → `{symbols: [...]}` |
| `tasks.tw_market_assistant.enabled` | `false` | Research-oriented TW digest |
| `tasks.tw_market_assistant.timezone` | `Asia/Taipei` | Market timezone |
| `tasks.tw_market_assistant.market_hours_only` | `true` | Skip when market closed/holiday |
| `tasks.tw_market_assistant.top_n` | `20` | Movers per exchange |
| `tasks.tw_market_assistant.notify_language` | `zh-TW` | Digest language |
| `tasks.tw_market_assistant.sources` | `twse`, `tpex` | Quote sources |

### Tasks — job research agent

| Key | Default | Description |
|-----|---------|-------------|
| `tasks.job_research_agent.enabled` | `true` | Bounded LangGraph/plain pilot |
| `tasks.job_research_agent.telegram_enabled` | `false` | Telegram delivery for the pilot |
| `tasks.job_research_agent.max_steps` | `12` | Workflow step budget |
| `tasks.job_research_agent.max_tool_calls` | `6` | Tool-call budget |
| `tasks.job_research_agent.max_llm_calls` | `3` | LLM-call budget |
| `tasks.job_research_agent.memory_namespace` | `job_research` | Memory namespace to ingest |
| `tasks.job_research_agent.llm_provider` | omitted | Override `llm.provider` for this workflow |
| `tasks.job_research_agent.llm_model` | omitted | Override `llm.model` for this workflow |

### Memory, investment, notifiers

| Key | Default | Description |
|-----|---------|-------------|
| `memory.namespaces` | `{}` | Map of namespace → document list |
| `memory.namespaces.<name>.documents` | `[]` | Explicit Markdown/text paths (never secrets) |
| `investment.watchlist` | `[]` | Ticker codes for theme/research helpers |
| `investment.theme_mappings` | `{}` | Map of theme → `{label, keywords}` |
| `notifiers.telegram.enabled` | `true` | Outbound Telegram digests |
| `notifiers.telegram.failure_alerts_enabled` | same as `enabled` | Task-failure alerts |

### Scoring

| Key | Default | Description |
|-----|---------|-------------|
| `scoring.freshness_weight` | `0.6` | Rule scorer weight |
| `scoring.priority_weight` | `0.4` | Rule scorer weight |
| `scoring.llm_enabled` | `false` | Optional LLM second stage |
| `scoring.llm_model` | synced with `llm.model` | Alias for gateway model |
| `scoring.llm_min_rule_score` | `25` | Minimum rule score before LLM |
| `scoring.llm_max_jobs_per_run` | `20` | LLM budget per run |
| `scoring.digest_max_strong_matches` | `10` | Digest cap |
| `scoring.digest_max_maybe_matches` | `10` | Digest cap |
| `scoring.include_rejected_summary` | `true` | Include rejected section |

### LLM gateway

| Key | Default | Description |
|-----|---------|-------------|
| `llm.provider` | `openai` | `openai` / `anthropic` / `fake` |
| `llm.model` | vendor default | Gateway model id |
| `llm.timeout_seconds` | `20` | LLM HTTP timeout |
| `llm.temperature` | `0.2` | OpenAI only; Anthropic ignores |
| `llm.max_retries` | `1` | Provider HTTP retries |
| `llm.fallbacks` | `[]` | Ordered `provider/model` fallbacks |

### Tools, browser, skills

| Key | Default | Description |
|-----|---------|-------------|
| `tools.allowlist` | `search_jobs`, `get_tw_stock_movers`, `query_recent_runs` | Agent-callable tools |
| `tools.timeout_seconds` | `30` | Per-tool timeout |
| `tools.permissions.classes` | `read=allow`, `notify=ask`, `write=disabled`, `execute=disabled` | Permission class policy |
| `tools.permissions.tools` | `{}` | Per-tool overrides (`disabled`/`ask`/`allow`) |
| `tools.permissions.max_side_effect_actions` | `3` | Cap on notify/write/execute actions |
| `browser.enabled` | `false` | Optional Chromium tool service |
| `browser.headless` | `true` | Headless Chromium |
| `browser.backend` | omitted | `fake` forces fixtures; else env / Chromium |
| `browser.allowed_domains` | `[]` | Navigation allowlist |
| `browser.denied_domains` | `[]` | Navigation denylist |
| `browser.navigation_timeout_seconds` | `15` | Page load timeout |
| `browser.max_page_bytes` | `500000` | Captured page size cap |
| `browser.allow_downloads` | `false` | Downloads always denied (`true` is rejected) |
| `browser.allow_javascript` | `false` | JS execution |
| `skills.enabled` | omitted → builtin defaults | Explicit skill enable list (`job_search`, `stock_monitoring`, …) |

### Sources

HTTP collectors share `timeout_seconds`, `max_retries`, `request_delay_seconds`.
Job boards also have `page_limit`; TW quote sources have `batch_size`.

| Key | Default | Description |
|-----|---------|-------------|
| `sources.job104.timeout_seconds` | `10` | HTTP timeout |
| `sources.job104.max_retries` | `2` | HTTP retries |
| `sources.job104.request_delay_seconds` | `1.0` | Delay between requests |
| `sources.job104.page_limit` | `1` | Pages to fetch |
| `sources.indeed.*` | same as job104 | Best-effort Indeed collector |
| `sources.glassdoor.*` | same as job104 | Best-effort Glassdoor collector |
| `sources.linkedin_email.lookback_days` | `14` | IMAP `SINCE` window |
| `sources.linkedin_email.imap_mailbox` | omitted → env/`INBOX` | Mailbox name |
| `sources.linkedin_email.timeout_seconds` | `30` | IMAP socket timeout |
| `sources.twse.timeout_seconds` | `10` | HTTP timeout |
| `sources.twse.max_retries` | `2` | HTTP retries |
| `sources.twse.request_delay_seconds` | `0.2` | Delay between batches |
| `sources.twse.batch_size` | `100` | Quotes per request |
| `sources.tpex.*` | same as twse | TPEx quote HTTP |

### Providers, search, coding runtime

| Key | Default | Description |
|-----|---------|-------------|
| `providers.openai.api_key_env` | `OPENAI_API_KEY` | Env var holding the OpenAI key |
| `providers.anthropic.api_key_env` | `ANTHROPIC_API_KEY` | Env var holding the Anthropic key |
| `providers.perplexity.api_key_env` | `PERPLEXITY_API_KEY` | Env var holding the Perplexity key |
| `providers.codex.api_key_env` | `CODEX_API_KEY` | Env var holding the Codex key |
| `search.provider` | `fake` | `fake` or `perplexity` |
| `search.model` | `sonar` | Perplexity model id |
| `search.max_results` | `5` | Research result cap |
| `search.timeout_seconds` | `20` | Search HTTP timeout |
| `runtime.default` | `builtin` | Bounded coding-analysis runtime (`builtin` / `codex` / `fake`) |
| `runtime.model` | omitted | Optional Codex model override |

## Example shape

See the repository root `config.yaml` for a full working example. Minimal Telegram-ready fragment:

```yaml
notifiers:
  telegram:
    enabled: true
    failure_alerts_enabled: true

llm:
  provider: fake   # or openai / anthropic with a key in .env

scoring:
  llm_enabled: false
```

## Notes

- A missing LLM key does **not** abort `job_search`: scoring falls open to rules.
- `signal-agent doctor` checks providers, Telegram, DB, browser, skills, and
  Taiwan market holiday calendar coverage.
- The local runtime writes JSON to `logs/signal-agent.log` via one
  `RotatingFileHandler`. Do not also copytruncate that same file with host
  logrotate; use `deploy/ubuntu/logrotate-signal-agent.conf` for cron wrapper
  logs only.
- k3s secrets: copy `deploy/k3s/secret.example.yaml` → `secret.yaml` (gitignored).
