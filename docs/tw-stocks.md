# Taiwan stocks

Tasks:

| Task | Purpose |
|------|---------|
| `tw_stock_movers` | Top gainers/losers digest (Traditional Chinese) |
| `stock_watchlist` | Personal symbol threshold alerts |
| `tw_market_assistant` | Research-oriented market digest |

All three respect Taiwan market hours unless a task explicitly disables that
guard.

## Market hours

Monday–Friday, **09:00–13:30** `Asia/Taipei`, excluding holidays and
settlement-only no-trade days.

Skip reasons:

| Reason | Meaning |
|--------|---------|
| `taiwan_market_holiday` | Date listed in the holiday YAML |
| `taiwan_market_closed` | Weekend or outside session |
| `taiwan_market_calendar_unavailable` | No YAML rows for that year (fail closed) |

`signal-agent doctor` warns when the packaged calendar is missing the current
year or is within ~60 days of the end of the latest covered year.

## Updating the holiday calendar

File: `app/data/tw_market_holidays.yaml` (ships with the package).

When TWSE publishes the next year
([holiday schedule](https://www.twse.com.tw/holidaySchedule/holidaySchedule)):

1. Add each no-trade date under `holidays:` with `date` (`YYYY-MM-DD`) and `name`.
2. Include settlement-only days (市場無交易，僅辦理結算交割作業).
3. Skip ordinary weekends; weekday/time checks already cover them.
4. Leave “開始交易日” / “最後交易日” informational rows out — those are trading days.
5. Add ad-hoc closures (typhoon, etc.) when announced.
6. Run `make check` and commit.

2027 entries may be provisional until TWSE publishes `queryYear=2027` open data —
verify against the official schedule and replace as needed.

## Watchlist

```yaml
tasks:
  stock_watchlist:
    enabled: true
    symbols:
      - code: "2330"
        name: 台積電
        alert_change_percent_abs: 2.0
```

## Run

```bash
signal-agent run tw_stock_movers
signal-agent run stock_watchlist
signal-agent run tw_market_assistant
```
