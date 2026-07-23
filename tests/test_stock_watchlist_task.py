from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core import SignalItem
from app.sources import BaseSource
from app.tasks import StockWatchlistTask
from app.tasks import stock_watchlist as watchlist_module

SAMPLE_OBSERVED_AT = datetime(2026, 6, 25, 2, 20, tzinfo=UTC)


class TwseWatchlistSource(BaseSource):
    name = "twse"

    def __init__(self, items: list[SignalItem]) -> None:
        self._items = items

    def fetch(self) -> list[SignalItem]:
        return list(self._items)


class TpexWatchlistSource(BaseSource):
    name = "tpex"

    def __init__(self, items: list[SignalItem] | None = None) -> None:
        self._items = items or []

    def fetch(self) -> list[SignalItem]:
        return list(self._items)


class FailingTwseSource(BaseSource):
    name = "twse"

    def fetch(self) -> list[SignalItem]:
        raise RuntimeError("twse quote provider timeout")


def _quote_item(
    *,
    stock_code: str,
    company: str,
    change_percent: float,
    market: str = "twse",
    observed_at: datetime = SAMPLE_OBSERVED_AT,
) -> SignalItem:
    return SignalItem(
        source=market,
        item_type="tw_stock_mover",
        title=f"{company} {change_percent:+.2f}%",
        observed_at=observed_at,
        external_id=f"{market}-20260625-1020-{stock_code}",
        content=f"{stock_code} quote",
        metadata={
            "stock_code": stock_code,
            "company": company,
            "change_percent": change_percent,
            "market": market,
            "price": 100.0,
        },
    )


def _write_config(
    tmp_path: Path,
    *,
    telegram_enabled: bool = False,
    enabled: bool = True,
) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  timezone: Asia/Taipei",
                '  digest_time: "20:00"',
                "tasks:",
                "  job_search:",
                "    enabled: true",
                "    sources:",
                "      - job104",
                "    keywords:",
                "      - python",
                "  tw_stock_movers:",
                "    enabled: true",
                "    top_n: 20",
                "    timezone: Asia/Taipei",
                "  stock_watchlist:",
                f"    enabled: {'true' if enabled else 'false'}",
                "    timezone: Asia/Taipei",
                "    sources:",
                "      - twse",
                "      - tpex",
                "    symbols:",
                '      - code: "2330"',
                "        name: 台積電",
                "        alert_change_percent_abs: 2.0",
                '      - code: "3081"',
                "        name: 聯亞",
                "        alert_change_percent_abs: 3.0",
                "    themes:",
                "      AI_infrastructure:",
                '        symbols: ["2330"]',
                "      CPO_optical:",
                '        symbols: ["3081"]',
                "notifiers:",
                "  telegram:",
                f"    enabled: {'true' if telegram_enabled else 'false'}",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
                "  llm_enabled: false",
                "  llm_model: gpt-4o-mini",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _task_run_statuses(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT status FROM task_runs ORDER BY id"
        ).fetchall()
    return [row[0] for row in rows]


def _latest_task_error(db_path: Path) -> str | None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT error_message FROM task_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return None if row is None else row[0]


def _count_alerts(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM signals WHERE item_type = ?",
            ("stock_watchlist_alert",),
        ).fetchone()
    return int(row[0])


def _emit_logs(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    captured = capsys.readouterr()
    logs: list[dict] = []
    for line in captured.err.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "event" in payload:
            logs.append(payload)
    return logs


def test_stock_watchlist_task_skips_when_market_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(
        watchlist_module,
        "resolve_task_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(
        watchlist_module,
        "tw_market_skip_reason",
        lambda: "taiwan_market_closed",
    )

    StockWatchlistTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "Taiwan market is closed" in captured.out
    assert _task_run_statuses(db_path) == ["skipped"]
    assert _latest_task_error(db_path) == "taiwan_market_closed"


def test_stock_watchlist_task_triggers_threshold_alerts_and_sends_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=True)
    db_path = tmp_path / "signals.sqlite3"
    sent: list[str] = []

    class FakeNotifier:
        def send_message(self, text: str) -> None:
            sent.append(text)

    monkeypatch.setattr(
        watchlist_module,
        "resolve_task_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(watchlist_module, "tw_market_skip_reason", lambda: None)
    monkeypatch.setattr(
        watchlist_module,
        "build_tw_stock_mover_sources_for_names",
        lambda _config, _names: [
            TwseWatchlistSource(
                [
                    _quote_item(
                        stock_code="2330",
                        company="台積電",
                        change_percent=-2.2,
                    ),
                    _quote_item(
                        stock_code="3081",
                        company="聯亞",
                        change_percent=4.12,
                    ),
                    _quote_item(
                        stock_code="2454",
                        company="聯發科",
                        change_percent=5.0,
                    ),
                ]
            ),
            TpexWatchlistSource(),
        ],
    )
    monkeypatch.setattr(
        "app.tasks.market_task_support.build_telegram_notifier",
        lambda env=None: FakeNotifier(),
    )
    monkeypatch.setattr(
        watchlist_module,
        "validate_telegram_notifier_secrets",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        watchlist_module,
        "load_env_settings",
        lambda: object(),
    )

    StockWatchlistTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "triggered 2 alert(s)" in captured.out
    assert _count_alerts(db_path) == 2
    assert len(sent) == 1
    digest = sent[0]
    assert digest.startswith("自選股異動快報 ")
    assert "價格異動" in digest
    assert "1. 3081 聯亞 +4.12% — 超過設定門檻 3.0%" in digest
    assert "2. 2330 台積電 -2.20% — 超過設定門檻 2.0%" in digest
    assert "主題觀察" in digest
    assert "CPO_optical: 1 檔觸發異動" in digest
    assert "AI_infrastructure: 1 檔觸發異動" in digest
    assert "2454" not in digest
    assert _task_run_statuses(db_path) == ["success"]


def test_stock_watchlist_task_skips_telegram_when_no_alerts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=True)
    db_path = tmp_path / "signals.sqlite3"
    sent: list[str] = []

    class FakeNotifier:
        def send_message(self, text: str) -> None:
            sent.append(text)

    monkeypatch.setattr(
        watchlist_module,
        "resolve_task_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(watchlist_module, "tw_market_skip_reason", lambda: None)
    monkeypatch.setattr(
        watchlist_module,
        "build_tw_stock_mover_sources_for_names",
        lambda _config, _names: [
            TwseWatchlistSource(
                [
                    _quote_item(
                        stock_code="2330",
                        company="台積電",
                        change_percent=1.5,
                    ),
                    _quote_item(
                        stock_code="3081",
                        company="聯亞",
                        change_percent=-2.5,
                    ),
                ]
            )
        ],
    )
    monkeypatch.setattr(
        "app.tasks.market_task_support.build_telegram_notifier",
        lambda env=None: FakeNotifier(),
    )

    StockWatchlistTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "triggered 0 alert(s)" in captured.out
    assert "telegram digest skipped: no new watchlist alerts" in captured.out
    assert sent == []
    assert _count_alerts(db_path) == 0


def test_stock_watchlist_task_dedupes_same_minute_bucket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=True)
    db_path = tmp_path / "signals.sqlite3"
    sent: list[str] = []

    class FakeNotifier:
        def send_message(self, text: str) -> None:
            sent.append(text)

    quotes = [
        TwseWatchlistSource(
            [
                _quote_item(
                    stock_code="3081",
                    company="聯亞",
                    change_percent=4.12,
                )
            ]
        )
    ]
    monkeypatch.setattr(
        watchlist_module,
        "resolve_task_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(watchlist_module, "tw_market_skip_reason", lambda: None)
    monkeypatch.setattr(
        watchlist_module,
        "build_tw_stock_mover_sources_for_names",
        lambda _config, _names: quotes,
    )
    monkeypatch.setattr(
        "app.tasks.market_task_support.build_telegram_notifier",
        lambda env=None: FakeNotifier(),
    )
    monkeypatch.setattr(
        watchlist_module,
        "validate_telegram_notifier_secrets",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(watchlist_module, "load_env_settings", lambda: object())

    StockWatchlistTask(str(config_path)).run()
    StockWatchlistTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert _count_alerts(db_path) == 1
    assert len(sent) == 1
    assert "1 new / 0 duplicate" in captured.out
    assert "0 new / 1 duplicate" in captured.out
    assert "telegram digest skipped: no new watchlist alerts" in captured.out


def test_stock_watchlist_task_raises_when_all_sources_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(
        watchlist_module,
        "resolve_task_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(watchlist_module, "tw_market_skip_reason", lambda: None)
    monkeypatch.setattr(
        watchlist_module,
        "build_tw_stock_mover_sources_for_names",
        lambda _config, _names: [FailingTwseSource()],
    )

    task = StockWatchlistTask(str(config_path))
    with pytest.raises(ValueError, match="All configured stock watchlist sources"):
        task.run()

    assert _task_run_statuses(db_path) == ["failed"]


def test_build_telegram_digest_formats_theme_section() -> None:
    from app.config import StockWatchlistThemeConfig

    alerts = [
        SignalItem(
            source="stock_watchlist",
            item_type="stock_watchlist_alert",
            title="3081 聯亞 +4.12%",
            observed_at=SAMPLE_OBSERVED_AT,
            metadata={
                "stock_code": "3081",
                "company": "聯亞",
                "change_percent": 4.12,
                "alert_change_percent_abs": 3.0,
            },
        )
    ]
    digest = watchlist_module._build_telegram_digest(
        alerts=alerts,
        themes={
            "CPO_optical": StockWatchlistThemeConfig(symbols=["3081"]),
            "AI_infrastructure": StockWatchlistThemeConfig(symbols=["2330"]),
        },
        timezone_name="Asia/Taipei",
    )
    assert "1. 3081 聯亞 +4.12% — 超過設定門檻 3.0%" in digest
    assert "CPO_optical: 1 檔觸發異動" in digest
    assert "AI_infrastructure" not in digest.split("主題觀察", 1)[-1]
