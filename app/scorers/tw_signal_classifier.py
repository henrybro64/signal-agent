"""Rule-based Taiwan stock theme classification and research candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from app.core import SignalItem
from app.core.config import ThemeMappingConfig
from app.core.tw_stock_utils import TwStockMarketReport
from app.core.tw_stock_utils import change_percent as stock_change_percent

FORBIDDEN_RECOMMENDATION_PHRASES: Final[tuple[str, ...]] = (
    "應該買進",
    "應該賣出",
    "建議買進",
    "建議賣出",
    "立即買進",
    "立即賣出",
)

HIGH_GAIN_RISK_THRESHOLD: Final[float] = 9.5
VOLUME_SPIKE_CHANGE_THRESHOLD: Final[float] = 3.0


@dataclass(slots=True)
class ResearchCandidate:
    """One stock flagged for follow-up research, not trading advice."""

    item: SignalItem
    status: str
    themes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)


def classify_item_themes(
    item: SignalItem,
    theme_mappings: dict[str, ThemeMappingConfig],
) -> list[str]:
    """Return theme labels whose keywords match the stock text."""

    if not theme_mappings:
        return []

    haystack = _item_search_text(item).lower()
    matched_labels: list[str] = []
    for mapping in theme_mappings.values():
        if any(keyword.lower() in haystack for keyword in mapping.keywords):
            matched_labels.append(mapping.label)
    return matched_labels


def find_watchlist_hits(
    items: list[SignalItem],
    watchlist: list[str],
) -> list[SignalItem]:
    """Return items whose stock code appears in the configured watchlist."""

    if not watchlist:
        return []

    watchlist_codes = {code.strip() for code in watchlist if code.strip()}
    hits: list[SignalItem] = []
    seen_codes: set[str] = set()
    for item in items:
        stock_code = _stock_code(item)
        if not stock_code or stock_code not in watchlist_codes:
            continue
        if stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        hits.append(item)
    return hits


def summarize_theme_activity(
    market_reports: list[TwStockMarketReport],
    theme_mappings: dict[str, ThemeMappingConfig],
) -> list[str]:
    """Build short bullets describing active themes in top gainers."""

    if not theme_mappings:
        return ["今日訊號以個股異動為主，可再觀察題材是否延續"]

    theme_counts: dict[str, int] = {}
    for report in market_reports:
        for item in report.gainers:
            for label in classify_item_themes(item, theme_mappings):
                theme_counts[label] = theme_counts.get(label, 0) + 1

    if not theme_counts:
        return ["漲幅榜以個股輪動為主，尚未出現明顯集中題材"]

    ranked = sorted(theme_counts.items(), key=lambda pair: (-pair[1], pair[0]))
    bullets: list[str] = []
    for label, count in ranked[:3]:
        bullets.append(f"{label} 相關個股在漲幅榜中較活躍（{count} 檔）")
    return bullets


def build_research_candidates(
    *,
    market_reports: list[TwStockMarketReport],
    watchlist_hits: list[SignalItem],
    theme_mappings: dict[str, ThemeMappingConfig],
    max_candidates: int = 5,
) -> list[ResearchCandidate]:
    """Pick deterministic research candidates from movers and watchlist hits."""

    candidates: list[ResearchCandidate] = []
    seen_codes: set[str] = set()

    for report in market_reports:
        for item in report.gainers:
            stock_code = _stock_code(item)
            if not stock_code or stock_code in seen_codes:
                continue
            seen_codes.add(stock_code)
            candidates.append(
                _build_candidate(
                    item,
                    theme_mappings=theme_mappings,
                    in_top_gainers=True,
                )
            )

    for item in watchlist_hits:
        stock_code = _stock_code(item)
        if not stock_code or stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        candidates.append(
            _build_candidate(
                item,
                theme_mappings=theme_mappings,
                in_top_gainers=False,
                watchlist_hit=True,
            )
        )

    candidates.sort(
        key=lambda candidate: (
            _candidate_priority(candidate),
            abs(stock_change_percent(candidate.item)),
        ),
        reverse=True,
    )
    return candidates[: max(max_candidates, 0)]


def assert_research_safe_text(text: str) -> None:
    """Raise when output contains direct trading recommendations."""

    for phrase in FORBIDDEN_RECOMMENDATION_PHRASES:
        if phrase in text:
            raise ValueError(f"Research output must not include '{phrase}'")


def _build_candidate(
    item: SignalItem,
    *,
    theme_mappings: dict[str, ThemeMappingConfig],
    in_top_gainers: bool,
    watchlist_hit: bool = False,
) -> ResearchCandidate:
    themes = classify_item_themes(item, theme_mappings)
    percent = stock_change_percent(item)
    reasons: list[str] = []
    follow_up: list[str] = []

    if in_top_gainers:
        reasons.append("漲幅進榜")
    if watchlist_hit:
        reasons.append("自選股異動")
    if _has_volume_signal(item):
        reasons.append("成交量相對放大")
    if themes:
        reasons.append(f"屬於 {' / '.join(themes)} 題材")

    if themes:
        follow_up.append("確認題材是否延續、是否有基本面或營收支持")
    else:
        follow_up.append("確認異動是否僅為短線題材或消息刺激")

    status = "值得研究"
    if percent >= HIGH_GAIN_RISK_THRESHOLD:
        status = "風險偏高"
        follow_up.append("漲幅已大，避免追高，等待回檔或量能確認")
    elif not themes and not watchlist_hit:
        status = "需要觀察"

    if not reasons:
        reasons.append("出現值得追蹤的價量異動")

    return ResearchCandidate(
        item=item,
        status=status,
        themes=themes,
        reasons=reasons,
        follow_up=follow_up,
    )


def _candidate_priority(candidate: ResearchCandidate) -> int:
    priority = 0
    if candidate.status == "值得研究":
        priority += 3
    elif candidate.status == "需要觀察":
        priority += 2
    if candidate.themes:
        priority += 2
    if any("自選股" in reason for reason in candidate.reasons):
        priority += 2
    if any("漲幅進榜" in reason for reason in candidate.reasons):
        priority += 1
    return priority


def _has_volume_signal(item: SignalItem) -> bool:
    volume = item.metadata.get("volume")
    percent = stock_change_percent(item)
    if not isinstance(volume, int | float) or volume <= 0:
        return percent >= VOLUME_SPIKE_CHANGE_THRESHOLD
    return percent >= VOLUME_SPIKE_CHANGE_THRESHOLD and volume >= 1_000


def _item_search_text(item: SignalItem) -> str:
    parts = [
        item.title,
        item.content or "",
        _company_name(item),
        str(item.metadata.get("industry_name") or ""),
        str(item.metadata.get("company_full_name") or ""),
    ]
    return " ".join(part for part in parts if part)


def _stock_code(item: SignalItem) -> str:
    stock_code = item.metadata.get("stock_code")
    if isinstance(stock_code, str):
        return stock_code.strip()
    return ""


def _company_name(item: SignalItem) -> str:
    company = item.metadata.get("company")
    if isinstance(company, str):
        return company.strip()
    return ""
