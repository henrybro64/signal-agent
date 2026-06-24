import sqlite3
from pathlib import Path

import pytest

from app.core import SignalItem
from app.sources import BaseSource, DummySource
from app.sources.dummy import SAMPLE_OBSERVED_AT
from app.tasks import TwStockMoversTask
from app.tasks import tw_stock_movers as tw_stock_movers_task_module


class StaticStockSource(BaseSource):
    name = "static_stock"

    def fetch(self) -> list[SignalItem]:
        return [
            SignalItem(
                source=self.name,
                item_type="tw_stock_mover",
                title="TSMC gains 3.1%",
                observed_at=SAMPLE_OBSERVED_AT,
                external_id="2330-1",
                content="Taiwan semiconductor leader jumps on AI demand.",
                metadata={
                    "stock_code": "2330",
                    "company": "TSMC",
                    "change_percent": 3.1,
                    "market": "twse",
                    "exchange": "tse",
                },
            ),
            SignalItem(
                source=self.name,
                item_type="tw_stock_mover",
                title="MediaTek falls 1.2%",
                observed_at=SAMPLE_OBSERVED_AT,
                external_id="2454-1",
                content="Pullback after a recent multi-day rally.",
                metadata={
                    "stock_code": "2454",
                    "company": "MediaTek",
                    "change_percent": -1.2,
                    "market": "twse",
                    "exchange": "tse",
                },
            ),
        ]


class TwseStockSource(BaseSource):
    name = "twse"

    def fetch(self) -> list[SignalItem]:
        return [
            _stock_mover_item(
                stock_code="2330",
                company="TSMC",
                change_percent=5.0,
                market="twse",
                exchange="tse",
            )
        ]


class TpexStockSource(BaseSource):
    name = "tpex"

    def fetch(self) -> list[SignalItem]:
        return [
            _stock_mover_item(
                stock_code="3105",
                company="穩懋",
                change_percent=10.0,
                market="tpex",
                exchange="otc",
            )
        ]


class FailingTpexSource(BaseSource):
    name = "tpex"

    def fetch(self) -> list[SignalItem]:
        raise RuntimeError("tpex quote provider timeout")


class FailingStockSource(BaseSource):
    name = "failing_stock"

    def fetch(self) -> list[SignalItem]:
        raise RuntimeError("quote provider timeout")


class HistoricalTwseStockSource(BaseSource):
    name = "twse"

    def fetch(self) -> list[SignalItem]:
        return [
            SignalItem(
                source=self.name,
                item_type="tw_stock_mover",
                title="TSMC gains 3.1%",
                observed_at=SAMPLE_OBSERVED_AT,
                external_id="twse-20260616-090000-2330",
                url=(
                    "https://www.twse.com.tw/zh/page/trading/exchange/STOCK_DAY.html"
                    "?date=20260616&stockNo=2330&snapshot=090000"
                ),
                content="TWSE 2330 last 1000.00, prev close 970.00.",
                metadata={
                    "stock_code": "2330",
                    "company": "TSMC",
                    "change_percent": 3.1,
                    "market": "twse",
                    "exchange": "tse",
                },
            ),
            SignalItem(
                source=self.name,
                item_type="tw_stock_mover",
                title="TSMC gains 3.1%",
                observed_at=SAMPLE_OBSERVED_AT.replace(minute=10),
                external_id="twse-20260616-091000-2330",
                url=(
                    "https://www.twse.com.tw/zh/page/trading/exchange/STOCK_DAY.html"
                    "?date=20260616&stockNo=2330&snapshot=091000"
                ),
                content="TWSE 2330 last 1000.00, prev close 970.00.",
                metadata={
                    "stock_code": "2330",
                    "company": "TSMC",
                    "change_percent": 3.1,
                    "market": "twse",
                    "exchange": "tse",
                },
            ),
        ]


def test_tw_stock_movers_task_skips_when_market_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=False)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: False,
    )

    TwStockMoversTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "Taiwan market is closed" in captured.out
    assert not db_path.exists()


def test_tw_stock_movers_task_filters_and_persists_stock_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=False)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: True,
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_tw_stock_mover_sources",
        lambda _config: [DummySource()],
    )

    TwStockMoversTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "tw_stock_movers task executed using" in captured.out
    assert "fetched 1 stock mover(s) from 1 successful source(s)" in captured.out
    assert "persisted 1 new / 0 duplicate" in captured.out
    assert (
        "reporting 1 ranked mover(s) across 1 market(s) (limit=20 each)"
        in captured.out
    )
    assert "ignored 1 non-stock signal(s)" in captured.out
    assert "TWSE listed (上市)" in captured.out
    assert "2330 TSMC +3.1% | TSMC gains 3.1%" in captured.out
    assert "telegram digest skipped: notifier disabled in config" in captured.out
    assert _count_rows(db_path, "signals") == 1
    assert _signal_run_statuses(db_path) == ["completed"]


def test_tw_stock_movers_task_sends_digest_with_top_n_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(
        tmp_path,
        telegram_enabled=True,
        top_n=1,
    )
    db_path = tmp_path / "signals.sqlite3"
    sent_messages: list[str] = []

    class FakeNotifier:
        def send_message(self, message: str) -> None:
            sent_messages.append(message)

    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: True,
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_tw_stock_mover_sources",
        lambda _config: [StaticStockSource()],
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_telegram_notifier",
        lambda *, env: FakeNotifier(),
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    TwStockMoversTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "telegram digest sent" in captured.out
    assert len(sent_messages) == 1
    assert sent_messages[0].startswith("台股漲跌幅快報 ")
    assert "上市前1漲幅" in sent_messages[0]
    assert "上市前1跌幅" in sent_messages[0]
    assert "1. 2330 TSMC +3.10%" in sent_messages[0]
    assert "1. 2454 MediaTek -1.20%" in sent_messages[0]


def test_tw_stock_movers_task_raises_when_all_sources_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=False)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: True,
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_tw_stock_mover_sources",
        lambda _config: [FailingStockSource()],
    )

    with pytest.raises(
        ValueError,
        match="All configured Taiwan stock-mover sources failed",
    ):
        TwStockMoversTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "source failed: failing_stock: quote provider timeout" in captured.out
    assert _signal_run_statuses(db_path) == ["failed"]


def test_tw_stock_movers_task_sends_failure_digest_when_all_sources_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=True)
    db_path = tmp_path / "signals.sqlite3"
    sent_messages: list[str] = []

    class FakeNotifier:
        def send_message(self, message: str) -> None:
            sent_messages.append(message)

    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: True,
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_tw_stock_mover_sources",
        lambda _config: [FailingStockSource()],
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_telegram_notifier",
        lambda *, env: FakeNotifier(),
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    with pytest.raises(
        ValueError,
        match="All configured Taiwan stock-mover sources failed",
    ):
        TwStockMoversTask(str(config_path)).run()

    assert len(sent_messages) == 1
    assert sent_messages[0].startswith("台股漲跌幅快報 ")
    assert "資料來源異常" in sent_messages[0]
    assert "quote provider timeout" in sent_messages[0]


def test_select_top_gainers_limits_to_top_n() -> None:
    items = [
        _stock_mover_item(
            stock_code=f"{index:04d}",
            company=f"Gainer {index}",
            change_percent=float(index),
        )
        for index in range(1, 26)
    ]

    top_gainers = tw_stock_movers_task_module._select_top_gainers(items, limit=20)

    assert len(top_gainers) == 20
    assert top_gainers[0].metadata["change_percent"] == 25.0
    assert top_gainers[-1].metadata["change_percent"] == 6.0


def test_select_top_losers_limits_to_top_n() -> None:
    items = [
        _stock_mover_item(
            stock_code=f"{index:04d}",
            company=f"Loser {index}",
            change_percent=-float(index),
        )
        for index in range(1, 26)
    ]

    top_losers = tw_stock_movers_task_module._select_top_losers(items, limit=20)

    assert len(top_losers) == 20
    assert top_losers[0].metadata["change_percent"] == -25.0
    assert top_losers[-1].metadata["change_percent"] == -6.0


def test_select_top_gainers_ranks_each_market_separately() -> None:
    items = [
        _stock_mover_item(
            stock_code="2330",
            company="TSMC",
            change_percent=5.0,
            market="twse",
            exchange="tse",
        ),
        _stock_mover_item(
            stock_code="3105",
            company="穩懋",
            change_percent=10.0,
            market="tpex",
            exchange="otc",
        ),
    ]

    reports = tw_stock_movers_task_module._build_market_reports(items, limit=20)

    twse_report = reports[0]
    tpex_report = reports[1]
    assert twse_report.market_key == "twse"
    assert tpex_report.market_key == "tpex"
    assert len(twse_report.gainers) == 1
    assert twse_report.gainers[0].metadata["stock_code"] == "2330"
    assert len(tpex_report.gainers) == 1
    assert tpex_report.gainers[0].metadata["stock_code"] == "3105"
    assert twse_report.losers == []
    assert tpex_report.losers == []


def test_tw_stock_movers_task_reports_both_markets_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=True, top_n=1)
    db_path = tmp_path / "signals.sqlite3"
    sent_messages: list[str] = []

    class FakeNotifier:
        def send_message(self, message: str) -> None:
            sent_messages.append(message)

    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: True,
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_tw_stock_mover_sources",
        lambda _config: [TwseStockSource(), TpexStockSource()],
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_telegram_notifier",
        lambda *, env: FakeNotifier(),
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    TwStockMoversTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "reporting 2 ranked mover(s) across 2 market(s)" in captured.out
    assert "TWSE listed (上市)" in captured.out
    assert "TPEx OTC (上櫃)" in captured.out
    assert "2330 TSMC +5.0%" in captured.out
    assert "3105 穩懋 +10.0%" in captured.out
    assert len(sent_messages) == 1
    assert "上市前1漲幅" in sent_messages[0]
    assert "上櫃前1漲幅" in sent_messages[0]
    assert "1. 2330 TSMC +5.00%" in sent_messages[0]
    assert "1. 3105 穩懋 +10.00%" in sent_messages[0]


def test_tw_stock_movers_task_continues_when_one_market_source_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=False)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: True,
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_tw_stock_mover_sources",
        lambda _config: [TwseStockSource(), FailingTpexSource()],
    )

    TwStockMoversTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "source failed: tpex: tpex quote provider timeout" in captured.out
    assert "2330 TSMC +5.0%" in captured.out
    assert "TPEx OTC (上櫃)" not in captured.out
    assert _signal_run_statuses(db_path) == ["completed", "failed"]


def test_tw_stock_movers_task_persists_multiple_snapshot_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=False)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: True,
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_tw_stock_mover_sources",
        lambda _config: [HistoricalTwseStockSource()],
    )

    TwStockMoversTask(str(config_path)).run()

    assert _count_rows(db_path, "signals") == 2


def test_tw_stock_movers_task_sends_partial_failure_warning_in_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, telegram_enabled=True, top_n=1)
    db_path = tmp_path / "signals.sqlite3"
    sent_messages: list[str] = []

    class FakeNotifier:
        def send_message(self, message: str) -> None:
            sent_messages.append(message)

    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: True,
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_tw_stock_mover_sources",
        lambda _config: [TwseStockSource(), FailingTpexSource()],
    )
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "build_telegram_notifier",
        lambda *, env: FakeNotifier(),
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")

    TwStockMoversTask(str(config_path)).run()

    assert len(sent_messages) == 1
    assert "上市前1漲幅" in sent_messages[0]
    assert "1. 2330 TSMC +5.00%" in sent_messages[0]
    assert "資料來源異常" in sent_messages[0]
    assert "上櫃資料：tpex quote provider timeout" in sent_messages[0]


def test_tw_stock_movers_task_respects_disabled_config(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        telegram_enabled=False,
        tw_stock_movers_enabled=False,
    )

    with pytest.raises(ValueError, match="Task 'tw_stock_movers' is disabled"):
        TwStockMoversTask(str(config_path)).run()


def test_tw_stock_movers_task_does_not_require_openai_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(
        tmp_path,
        telegram_enabled=False,
        llm_enabled=True,
    )
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(tw_stock_movers_task_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(
        tw_stock_movers_task_module,
        "is_tw_market_open",
        lambda: False,
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    TwStockMoversTask(str(config_path)).run()

    captured = capsys.readouterr()
    assert "Taiwan market is closed" in captured.out
    assert not db_path.exists()


def _stock_mover_item(
    *,
    stock_code: str,
    company: str,
    change_percent: float,
    market: str = "twse",
    exchange: str = "tse",
) -> SignalItem:
    direction = "gains" if change_percent > 0 else "falls"
    return SignalItem(
        source=market,
        item_type="tw_stock_mover",
        title=f"{company} {direction} {abs(change_percent):.1f}%",
        observed_at=SAMPLE_OBSERVED_AT,
        external_id=f"{stock_code}-sample",
        content=f"{company} moved {change_percent:+.1f}%.",
        metadata={
            "stock_code": stock_code,
            "company": company,
            "change_percent": change_percent,
            "market": market,
            "exchange": exchange,
        },
    )


def _write_config(
    tmp_path: Path,
    *,
    telegram_enabled: bool,
    top_n: int = 20,
    tw_stock_movers_enabled: bool = True,
    llm_enabled: bool = False,
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
                "      - dummy",
                "    keywords:",
                "      - python",
                "  tw_stock_movers:",
                f"    enabled: {'true' if tw_stock_movers_enabled else 'false'}",
                f"    top_n: {top_n}",
                "    timezone: Asia/Taipei",
                "notifiers:",
                "  telegram:",
                f"    enabled: {'true' if telegram_enabled else 'false'}",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
                f"  llm_enabled: {'true' if llm_enabled else 'false'}",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _count_rows(db_path: Path, table_name: str) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            f"SELECT COUNT(*) FROM {table_name}"
        ).fetchone()
    assert row is not None
    return int(row[0])


def _signal_run_statuses(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT status FROM signal_runs ORDER BY id"
        ).fetchall()
    return [str(row[0]) for row in rows]
