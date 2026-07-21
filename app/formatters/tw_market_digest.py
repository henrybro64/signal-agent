"""Traditional Chinese Telegram digest for the Taiwan market assistant."""

from __future__ import annotations

from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from app.core import SignalItem
from app.core.tw_stock_utils import (
    TwStockMarketReport,
    change_percent,
    company_name_for_item,
    render_digest_ranked_line,
    stock_code_for_item,
)
from app.scorers.tw_signal_classifier import (
    ResearchCandidate,
    assert_research_safe_text,
)
from app.tasks.tw_stock_movers import TwStockMoversSummary

_MARKET_SECTION_LABELS: Final[dict[str, tuple[str, str]]] = {
    "twse": ("上市前20漲幅", "上市前20跌幅"),
    "tpex": ("上櫃前20漲幅", "上櫃前20跌幅"),
}
_SOURCE_WARNING_LABELS: Final[dict[str, str]] = {
    "twse": "上市資料",
    "tpex": "上櫃資料",
}


def build_tw_market_assistant_digest(
    *,
    summary: TwStockMoversSummary,
    market_reports: list[TwStockMarketReport],
    summary_bullets: list[str],
    watchlist_hits: list[SignalItem],
    research_candidates: list[ResearchCandidate],
    top_n: int,
    timezone_name: str,
) -> str:
    """Build the research-oriented market assistant Telegram report."""

    reported_count = sum(report.reported_count for report in market_reports)
    if reported_count == 0 and not summary.failed_sources:
        return ""

    lines = [_build_heading(timezone_name)]

    lines.append("")
    lines.append("一、盤中異動摘要")
    if summary_bullets:
        lines.extend(f"- {bullet}" for bullet in summary_bullets)
    else:
        lines.append("- 今日尚未整理出明顯題材集中方向")
    if watchlist_hits:
        watchlist_labels = [
            _format_stock_label(item) for item in watchlist_hits[:5]
        ]
        lines.append(f"- 自選股異動：{', '.join(watchlist_labels)}")

    section_number = 2
    for report in market_reports:
        gainers_title, losers_title = _MARKET_SECTION_LABELS.get(
            report.market_key,
            (f"{report.title}前{top_n}漲幅", f"{report.title}前{top_n}跌幅"),
        )
        if report.gainers:
            lines.append("")
            lines.append(f"{_section_prefix(section_number)}、{gainers_title}")
            section_number += 1
            for index, item in enumerate(report.gainers, start=1):
                lines.append(_render_ranked_line_with_volume(index, item))

        if report.losers:
            lines.append("")
            lines.append(f"{_section_prefix(section_number)}、{losers_title}")
            section_number += 1
            for index, item in enumerate(report.losers, start=1):
                lines.append(_render_ranked_line_with_volume(index, item))

    lines.append("")
    lines.append(f"{_section_prefix(section_number)}、值得研究候選")
    section_number += 1
    if research_candidates:
        for index, candidate in enumerate(research_candidates, start=1):
            lines.extend(_render_research_candidate(index, candidate))
    else:
        lines.append("- 今日暫無明確研究候選，可持續觀察盤中量能變化")

    if summary.failed_sources:
        lines.append("")
        lines.append(f"{_section_prefix(section_number)}、資料來源異常")
        section_number += 1
        for source_name, message in summary.failed_sources:
            label = _SOURCE_WARNING_LABELS.get(source_name, source_name)
            lines.append(f"- {label}：{message}")

    lines.append("")
    lines.append(f"{_section_prefix(section_number)}、風險提醒")
    lines.extend(
        [
            "- 本訊息只做研究輔助，不是買賣建議",
            "- 漲幅過大個股需避免追高",
            "- 請自行確認基本面、月營收與題材是否屬實",
        ]
    )

    digest = "\n".join(lines)
    assert_research_safe_text(digest)
    return digest


def _build_heading(timezone_name: str) -> str:
    timestamp = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")
    return f"台股投資助理快報 {timestamp}"


def _section_prefix(number: int) -> str:
    mapping = {
        1: "一",
        2: "二",
        3: "三",
        4: "四",
        5: "五",
        6: "六",
        7: "七",
    }
    return mapping.get(number, str(number))


def _render_ranked_line_with_volume(index: int, item: SignalItem) -> str:
    base_line = render_digest_ranked_line(index, item)
    volume = item.metadata.get("volume")
    if isinstance(volume, int | float) and volume > 0:
        return f"{base_line} 成交量 {int(volume):,}"
    return base_line


def _render_research_candidate(
    index: int,
    candidate: ResearchCandidate,
) -> list[str]:
    label = _format_stock_label(candidate.item)
    lines = [f"{index}. {label}"]
    lines.append(f"   狀態：{candidate.status}")
    if candidate.reasons:
        lines.append(f"   原因：{'、'.join(candidate.reasons)}")
    if candidate.follow_up:
        lines.append(f"   待確認：{'、'.join(candidate.follow_up)}")
    return lines


def _format_stock_label(item: SignalItem) -> str:
    stock_code = stock_code_for_item(item)
    company = company_name_for_item(item)
    percent = change_percent(item)
    prefix_parts = [part for part in (stock_code, company) if part]
    prefix = " ".join(prefix_parts).strip() or item.title.strip()
    return f"{prefix} {_format_signed_percent(percent)}"


def _format_signed_percent(change_percent: float) -> str:
    return f"{change_percent:+.2f}%"
