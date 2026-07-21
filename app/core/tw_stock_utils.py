"""Shared Taiwan stock mover ranking and formatting helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from app.core.models import SignalItem

_MARKET_SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("twse", "TWSE listed (上市)"),
    ("tpex", "TPEx OTC (上櫃)"),
)


@dataclass(slots=True)
class TwStockMarketReport:
    """Ranked gainers and losers for one Taiwan stock market."""

    market_key: str
    title: str
    gainers: list[SignalItem] = field(default_factory=list)
    losers: list[SignalItem] = field(default_factory=list)

    @property
    def reported_count(self) -> int:
        return len(self.gainers) + len(self.losers)


def build_market_reports(
    items: list[SignalItem],
    *,
    limit: int,
) -> list[TwStockMarketReport]:
    return [
        TwStockMarketReport(
            market_key=market_key,
            title=title,
            gainers=select_top_gainers(
                items,
                limit=limit,
                market_key=market_key,
            ),
            losers=select_top_losers(
                items,
                limit=limit,
                market_key=market_key,
            ),
        )
        for market_key, title in _MARKET_SECTIONS
    ]


def count_reported_movers(market_reports: list[TwStockMarketReport]) -> int:
    return sum(report.reported_count for report in market_reports)


def select_top_gainers(
    items: list[SignalItem],
    *,
    limit: int,
    market_key: str | None = None,
) -> list[SignalItem]:
    if limit <= 0 or not items:
        return []
    gainers = [
        item
        for item in items
        if change_percent(item) > 0
        and (market_key is None or market_key_for_item(item) == market_key)
    ]
    return sorted(
        gainers,
        key=lambda item: (change_percent(item), item.observed_at),
        reverse=True,
    )[:limit]


def select_top_losers(
    items: list[SignalItem],
    *,
    limit: int,
    market_key: str | None = None,
) -> list[SignalItem]:
    if limit <= 0 or not items:
        return []
    losers = [
        item
        for item in items
        if change_percent(item) < 0
        and (market_key is None or market_key_for_item(item) == market_key)
    ]
    return sorted(
        losers,
        key=lambda item: (change_percent(item), -item.observed_at.timestamp()),
    )[:limit]


def render_digest_ranked_line(index: int, item: SignalItem) -> str:
    stock_code = stock_code_for_item(item)
    company = company_name_for_item(item)
    percent = change_percent(item)

    prefix_parts = [part for part in (stock_code, company) if part]
    prefix = " ".join(prefix_parts).strip()
    if not prefix:
        prefix = item.title.strip()
    return f"{index}. {prefix} {percent:+.2f}%"


def change_percent(item: SignalItem) -> float:
    value = item.metadata.get("change_percent")
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def stock_code_for_item(item: SignalItem) -> str:
    stock_code = item.metadata.get("stock_code")
    if isinstance(stock_code, str):
        return stock_code.strip()
    return ""


def company_name_for_item(item: SignalItem) -> str:
    company = item.metadata.get("company")
    if isinstance(company, str):
        return company.strip()
    return ""


def market_key_for_item(item: SignalItem) -> str:
    market = item.metadata.get("market")
    if isinstance(market, str) and market.strip():
        return market.strip().lower()

    exchange = item.metadata.get("exchange")
    if isinstance(exchange, str):
        normalized_exchange = exchange.strip().lower()
        if normalized_exchange == "otc":
            return "tpex"
        if normalized_exchange == "tse":
            return "twse"

    if item.source in {"twse", "tpex"}:
        return item.source

    return item.source
