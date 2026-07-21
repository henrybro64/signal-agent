import sqlite3
from pathlib import Path

import pytest

from app.core import SignalItem
from app.core.tw_stock_utils import TwStockMarketReport
from app.formatters.tw_market_digest import build_tw_market_assistant_digest
from app.scorers.tw_signal_classifier import build_research_candidates
from app.sources.dummy import SAMPLE_OBSERVED_AT
from app.tasks import TwMarketAssistantTask
from app.tasks import tw_market_assistant as tw_market_assistant_module
from app.tasks.tw_stock_movers import TwStockMoversSummary


class TwseStockSource:
    name = "twse"

    def fetch(self) -> list[SignalItem]:
        return [
            _stock_item(
                stock_code="2330",
                company="台積電",
                change_percent=3.5,
                market="twse",
                volume=12000,
            ),
            _stock_item(
                stock_code="6666",
                company="光通訊標的",
                change_percent=8.2,
                market="twse",
                content="CPO 光通訊題材",
                volume=8000,
            ),
        ]


class TpexStockSource:
    name = "tpex"

    def fetch(self) -> list[SignalItem]:
        return [
            _stock_item(
                stock_code="3105",
                company="穩懋",
                change_percent=-2.5,
                market="tpex",
                volume=3000,
            )
        ]


class FailingTwseSource:
    name = "twse"

    def fetch(self) -> list[SignalItem]:
        raise RuntimeError("twse timeout")


def test_build_tw_market_assistant_digest_includes_research_and_risk_sections() -> None:
    summary = TwStockMoversSummary(
        successful_sources=2,
        fetched_count=3,
        stock_items=[],
    )
    reports = [
        TwStockMarketReport(
            market_key="twse",
            title="TWSE listed (上市)",
            gainers=[
                _stock_item("2330", "台積電", 3.5, market="twse", volume=12000)
            ],
            losers=[],
        ),
        TwStockMarketReport(
            market_key="tpex",
            title="TPEx OTC (上櫃)",
            gainers=[],
            losers=[
                _stock_item("3105", "穩懋", -2.5, market="tpex", volume=3000)
            ],
        ),
    ]
    candidates = build_research_candidates(
        market_reports=reports,
        watchlist_hits=[reports[0].gainers[0]],
        theme_mappings={},
        max_candidates=3,
    )

    digest = build_tw_market_assistant_digest(
        summary=summary,
        market_reports=reports,
        summary_bullets=["上市漲幅榜以個股輪動為主"],
        watchlist_hits=[reports[0].gainers[0]],
        research_candidates=candidates,
        top_n=20,
        timezone_name="Asia/Taipei",
    )

    assert "台股投資助理快報" in digest
    assert "一、盤中異動摘要" in digest
    assert "上市前20漲幅" in digest
    assert "上櫃前20跌幅" in digest
    assert "值得研究候選" in digest
    assert "風險提醒" in digest
    assert "不是買賣建議" in digest
    assert "自選股異動" in digest
    assert "應該買進" not in digest
    assert "應該賣出" not in digest


def test_tw_market_assistant_task_skips_when_market_is_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(tw_market_assistant_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(tw_market_assistant_module, "is_tw_market_open", lambda: False)

    TwMarketAssistantTask(config_path=str(config_path)).run()

    captured = capsys.readouterr()
    assert "tw_market_assistant task skipped" in captured.out
    assert "Taiwan market is closed" in captured.out


def test_tw_market_assistant_task_persists_snapshot_and_candidates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(tw_market_assistant_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(tw_market_assistant_module, "is_tw_market_open", lambda: True)
    monkeypatch.setattr(
        tw_market_assistant_module,
        "build_tw_stock_mover_sources_for_names",
        lambda _config, _names: [TwseStockSource(), TpexStockSource()],
    )
    monkeypatch.setattr(
        tw_market_assistant_module,
        "validate_telegram_notifier_secrets",
        lambda **kwargs: None,
    )

    sent_messages: list[str] = []

    class FakeNotifier:
        def send_message(self, text: str) -> None:
            sent_messages.append(text)

    monkeypatch.setattr(
        tw_market_assistant_module,
        "build_telegram_notifier",
        lambda env: FakeNotifier(),
    )

    TwMarketAssistantTask(config_path=str(config_path)).run()

    captured = capsys.readouterr()
    assert "tw_market_assistant task executed using" in captured.out
    assert "research candidates=" in captured.out
    assert sent_messages
    assert "台股投資助理快報" in sent_messages[0]

    with sqlite3.connect(db_path) as connection:
        snapshot_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM signals
            WHERE item_type = 'tw_market_assistant_snapshot'
            """
        ).fetchone()[0]
        candidate_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM signals
            WHERE item_type = 'tw_research_candidate'
            """
        ).fetchone()[0]

    assert snapshot_count == 1
    assert candidate_count >= 1


def test_tw_market_assistant_task_respects_disabled_config(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, enabled=False)

    with pytest.raises(ValueError, match="Task 'tw_market_assistant' is disabled"):
        TwMarketAssistantTask(config_path=str(config_path)).run()


def test_tw_market_assistant_task_raises_when_all_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path)
    db_path = tmp_path / "signals.sqlite3"
    monkeypatch.setattr(tw_market_assistant_module, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(tw_market_assistant_module, "is_tw_market_open", lambda: True)
    monkeypatch.setattr(
        tw_market_assistant_module,
        "build_tw_stock_mover_sources_for_names",
        lambda _config, _names: [FailingTwseSource()],
    )
    monkeypatch.setattr(
        tw_market_assistant_module,
        "validate_telegram_notifier_secrets",
        lambda **kwargs: None,
    )
    class FakeNotifier:
        def send_message(self, text: str) -> None:
            return None

    monkeypatch.setattr(
        tw_market_assistant_module,
        "build_telegram_notifier",
        lambda env: FakeNotifier(),
    )

    with pytest.raises(
        ValueError,
        match="All configured Taiwan stock-mover sources failed",
    ):
        TwMarketAssistantTask(config_path=str(config_path)).run()


def _stock_item(
    stock_code: str,
    company: str,
    change_percent: float,
    *,
    market: str,
    content: str = "",
    volume: int | None = None,
) -> SignalItem:
    metadata = {
        "stock_code": stock_code,
        "company": company,
        "change_percent": change_percent,
        "market": market,
        "exchange": "tse" if market == "twse" else "otc",
    }
    if volume is not None:
        metadata["volume"] = volume
    return SignalItem(
        source=market,
        item_type="tw_stock_mover",
        title=f"{company} {change_percent:+.1f}%",
        observed_at=SAMPLE_OBSERVED_AT,
        external_id=f"{stock_code}-test",
        content=content,
        metadata=metadata,
    )


def _write_config(tmp_path: Path, *, enabled: bool = True) -> Path:
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
                "  tw_market_assistant:",
                f"    enabled: {'true' if enabled else 'false'}",
                "    timezone: Asia/Taipei",
                "    market_hours_only: true",
                "    top_n: 20",
                "    notify_language: zh-TW",
                "    sources:",
                "      - twse",
                "      - tpex",
                "investment:",
                "  watchlist:",
                '    - "2330"',
                "  theme_mappings:",
                "    cpo_optical:",
                "      label: CPO / 光通訊",
                "      keywords:",
                "        - 光通訊",
                "        - CPO",
                "notifiers:",
                "  telegram:",
                "    enabled: true",
                "scoring:",
                "  freshness_weight: 0.6",
                "  priority_weight: 0.4",
            ]
        ),
        encoding="utf-8",
    )
    return config_path
