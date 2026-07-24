import pytest

from app.core import SignalItem
from app.core.config import ThemeMappingConfig
from app.core.tw_stock_utils import TwStockMarketReport
from app.scorers.tw_signal_classifier import (
    assert_research_safe_text,
    build_research_candidates,
    classify_item_themes,
    find_watchlist_hits,
    summarize_theme_activity,
)
from app.sources.dummy import SAMPLE_OBSERVED_AT


def _item(
    *,
    stock_code: str,
    company: str,
    change_percent: float,
    market: str = "twse",
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


def test_classify_item_themes_matches_configured_keywords() -> None:
    item = _item(
        stock_code="3450",
        company="聯鈞光電",
        change_percent=6.5,
        content="CPO 光通訊相關個股",
    )
    themes = classify_item_themes(
        item,
        {
            "cpo_optical": ThemeMappingConfig(
                label="CPO / 光通訊",
                keywords=["光通訊", "CPO"],
            )
        },
    )

    assert themes == ["CPO / 光通訊"]


def test_find_watchlist_hits_returns_unique_configured_codes() -> None:
    items = [
        _item(stock_code="2330", company="台積電", change_percent=2.0),
        _item(stock_code="2330", company="台積電", change_percent=2.1),
        _item(stock_code="2454", company="聯發科", change_percent=-1.0),
    ]

    hits = find_watchlist_hits(items, ["2330", "2454"])

    assert [item.metadata["stock_code"] for item in hits] == ["2330", "2454"]


def test_build_research_candidates_marks_high_gain_as_high_risk() -> None:
    reports = [
        TwStockMarketReport(
            market_key="twse",
            title="TWSE listed (上市)",
            gainers=[
                _item(
                    stock_code="6666",
                    company="追高標的",
                    change_percent=9.8,
                    volume=5000,
                )
            ],
            losers=[],
        )
    ]

    candidates = build_research_candidates(
        market_reports=reports,
        watchlist_hits=[],
        theme_mappings={},
        max_candidates=3,
    )

    assert len(candidates) == 1
    assert candidates[0].status == "風險偏高"
    assert "漲幅進榜" in candidates[0].reasons


def test_summarize_theme_activity_reports_active_themes() -> None:
    reports = [
        TwStockMarketReport(
            market_key="twse",
            title="TWSE listed (上市)",
            gainers=[
                _item(
                    stock_code="6666",
                    company="AI伺服器A",
                    change_percent=4.0,
                    content="AI 伺服器散熱",
                ),
                _item(
                    stock_code="7777",
                    company="AI伺服器B",
                    change_percent=3.0,
                    content="GPU 伺服器",
                ),
            ],
            losers=[],
        )
    ]
    theme_mappings = {
        "ai_server": ThemeMappingConfig(
            label="AI伺服器",
            keywords=["伺服器", "AI", "GPU"],
        )
    }

    bullets = summarize_theme_activity(reports, theme_mappings)

    assert any("AI伺服器" in bullet for bullet in bullets)


def test_assert_research_safe_text_rejects_direct_trading_advice() -> None:
    with pytest.raises(ValueError, match="應該買進"):
        assert_research_safe_text("這檔股票應該買進")


def test_classify_item_themes_does_not_match_short_keyword_inside_words() -> None:
    item = _item(
        stock_code="9999",
        company="Price Systems",
        change_percent=4.0,
        content="price momentum only",
    )
    themes = classify_item_themes(
        item,
        {
            "semiconductor": ThemeMappingConfig(
                label="半導體",
                keywords=["IC"],
            )
        },
    )

    assert themes == []


def test_build_research_candidates_omits_volume_reason_without_volume_data() -> None:
    reports = [
        TwStockMarketReport(
            market_key="twse",
            title="TWSE listed (上市)",
            gainers=[
                _item(
                    stock_code="8888",
                    company="無量能標的",
                    change_percent=4.5,
                )
            ],
            losers=[],
        )
    ]

    candidates = build_research_candidates(
        market_reports=reports,
        watchlist_hits=[],
        theme_mappings={},
        max_candidates=3,
    )

    assert len(candidates) == 1
    assert "成交量相對放大" not in candidates[0].reasons


def test_build_research_candidates_includes_volume_reason_with_volume_data() -> None:
    reports = [
        TwStockMarketReport(
            market_key="twse",
            title="TWSE listed (上市)",
            gainers=[
                _item(
                    stock_code="8888",
                    company="有量能標的",
                    change_percent=4.5,
                    volume=5000,
                )
            ],
            losers=[],
        )
    ]

    candidates = build_research_candidates(
        market_reports=reports,
        watchlist_hits=[],
        theme_mappings={},
        max_candidates=3,
    )

    assert len(candidates) == 1
    assert "成交量相對放大" in candidates[0].reasons
