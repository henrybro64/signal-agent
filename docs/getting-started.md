# Getting started

Goal: go from a fresh clone to a Telegram test message (and optionally a first digest).

## 1. Install

```bash
git clone https://github.com/henrybro64/signal-agent.git
cd signal-agent
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'
```

## 2. First-run setup

```bash
signal-agent setup --non-interactive --provider fake
signal-agent doctor
signal-agent config show
```

`setup` writes `config.yaml` and `.env`, and initializes SQLite under `data/`.
Re-running keeps existing `.env` secrets unless you pass `--force`.

For live LLM scoring later, re-run setup with `--provider openai` (or
`anthropic`) and put the matching API key in `.env`.

## 3. Create a Telegram bot and chat ID

Digests and failure alerts need Telegram credentials in `.env`:

1. Open Telegram and talk to [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, follow the prompts, and copy the **bot token**.
3. Start a chat with your new bot (press Start), or add it to a group.
4. Get your **chat ID**:
   - Easy path: message [@userinfobot](https://t.me/userinfobot) and copy your Id, **or**
   - API path: send any message to your bot, then open
     `https://api.telegram.org/bot<TOKEN>/getUpdates` and read
     `result[].message.chat.id` (negative for groups).

Put both values in `.env` (never commit this file):

```bash
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```

Enable the notifier (non-interactive setup defaults it off). Either re-run:

```bash
signal-agent setup --non-interactive --provider fake --telegram-enabled
```

or set it in `config.yaml`:

```yaml
notifiers:
  telegram:
    enabled: true
    failure_alerts_enabled: true
```

## 4. Verify Telegram

```bash
signal-agent doctor
python main.py --telegram-test
```

You should receive a short test message in Telegram. If it fails, see
[Troubleshooting](troubleshooting.md).

## 5. Run a first task

```bash
signal-agent tasks
signal-agent run dummy
# Optional: live job digest (needs sources + Telegram)
signal-agent run job_search
```

For Taiwan stock movers, run only during market hours (or with
`market_hours_only: false` on the assistant task). See [Taiwan stocks](tw-stocks.md).

## 6. Keep it running (optional)

```bash
signal-agent start          # local long-running worker
signal-agent status
signal-agent logs --lines 50
signal-agent stop
```

Persistent schedules: [Scheduling](scheduling.md).
Host install choices: [Deployment](deployment.md).

## Next steps

- Point job scoring at your own profile Markdown — [Job search](job-search.md)
- Fill `config.yaml` carefully — [Configuration](configuration.md)
- Fix common errors — [Troubleshooting](troubleshooting.md)
