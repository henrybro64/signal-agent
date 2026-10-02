"""Traditional Chinese Telegram digest for the Taiwan market assistant."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core import SignalItem
from app.core.tw_stock_utils import (
    TwStockMarketReport,
    TwStockMoversSummary,
    change_percent,
    company_name_for_item,
    render_digest_ranked_line,
    source_warning_label,
    stock_code_for_item,
)
from app.scorers.tw_signal_classifier import (
    ResearchCandidate,
    assert_research_safe_text,
)

_SECTION_PREFIXES = {
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
}


def _market_section_labels(top_n: int) -> dict[str, tuple[str, str]]:
    return {
        "twse": (f"上市前{top_n}漲幅", f"上市前{top_n}跌幅"),
        "tpex": (f"上櫃前{top_n}漲幅", f"上櫃前{top_n}跌幅"),
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
    notify_language: str = "zh-TW",
) -> str:
    """Build the research-oriented market assistant Telegram report."""

    language = (notify_language or "zh-TW").strip()
    if language.lower() not in {"zh-tw", "zh", "zh_tw", "zh-hant"}:
        raise ValueError(
            "tw_market_assistant currently supports notify_language zh-TW only "
            f"(got {notify_language!r})"
        )

    reported_count = sum(report.reported_count for report in market_reports)
    if reported_count == 0 and not summary.failed_sources:
        return ""

    lines = [_build_heading(timezone_name)]
    lines.extend(
        _build_summary_section(
            summary_bullets=summary_bullets,
            watchlist_hits=watchlist_hits,
        )
    )
    section_number = 2
    mover_lines, section_number = _build_mover_sections(
        market_reports=market_reports,
        top_n=top_n,
        section_number=section_number,
    )
    lines.extend(mover_lines)
    candidate_lines, section_number = _build_research_section(
        research_candidates=research_candidates,
        section_number=section_number,
    )
    lines.extend(candidate_lines)
    source_lines, section_number = _build_source_warning_section(
        failed_sources=summary.failed_sources,
        section_number=section_number,
    )
    lines.extend(source_lines)
    lines.extend(_build_risk_section(section_number))

    digest = "\n".join(lines)
    assert_research_safe_text(digest)
    return digest


def _build_heading(timezone_name: str) -> str:
    timestamp = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")
    return f"台股投資助理快報 {timestamp}"


def _build_summary_section(
    *,
    summary_bullets: list[str],
    watchlist_hits: list[SignalItem],
) -> list[str]:
    lines = ["", "一、盤中異動摘要"]
    if summary_bullets:
        lines.extend(f"- {bullet}" for bullet in summary_bullets)
    else:
        lines.append("- 今日尚未整理出明顯題材集中方向")
    if watchlist_hits:
        watchlist_labels = [
            _format_stock_label(item) for item in watchlist_hits[:5]
        ]
        lines.append(f"- 自選股異動：{', '.join(watchlist_labels)}")
    return lines


def _build_mover_sections(
    *,
    market_reports: list[TwStockMarketReport],
    top_n: int,
    section_number: int,
) -> tuple[list[str], int]:
    section_labels = _market_section_labels(top_n)
    lines: list[str] = []
    for report in market_reports:
        gainers_title, losers_title = section_labels.get(
            report.market_key,
            (f"{report.title}前{top_n}漲幅", f"{report.title}前{top_n}跌幅"),
        )
        if report.gainers:
            lines.append("")
            lines.append(f"{_section_prefix(section_number)}、{gainers_title}")
            section_number += 1
            lines.extend(
                _render_ranked_line_with_volume(index, item)
                for index, item in enumerate(report.gainers, start=1)
            )
        if report.losers:
            lines.append("")
            lines.append(f"{_section_prefix(section_number)}、{losers_title}")
            section_number += 1
            lines.extend(
                _render_ranked_line_with_volume(index, item)
                for index, item in enumerate(report.losers, start=1)
            )
    return lines, section_number


def _build_research_section(
    *,
    research_candidates: list[ResearchCandidate],
    section_number: int,
) -> tuple[list[str], int]:
    lines = ["", f"{_section_prefix(section_number)}、值得研究候選"]
    section_number += 1
    if research_candidates:
        for index, candidate in enumerate(research_candidates, start=1):
            lines.extend(_render_research_candidate(index, candidate))
    else:
        lines.append("- 今日暫無明確研究候選，可持續觀察盤中量能變化")
    return lines, section_number


def _build_source_warning_section(
    *,
    failed_sources: list[tuple[str, str]],
    section_number: int,
) -> tuple[list[str], int]:
    if not failed_sources:
        return [], section_number
    lines = ["", f"{_section_prefix(section_number)}、資料來源異常"]
    section_number += 1
    for source_name, message in failed_sources:
        lines.append(f"- {source_warning_label(source_name)}：{message}")
    return lines, section_number


def _build_risk_section(section_number: int) -> list[str]:
    return [
        "",
        f"{_section_prefix(section_number)}、風險提醒",
        "- 本訊息只做研究輔助，不是買賣建議",
        "- 漲幅過大個股需留意流動性與基本面風險",
        "- 請自行確認基本面、月營收與題材是否屬實",
    ]


def _section_prefix(number: int) -> str:
    return _SECTION_PREFIXES.get(number, str(number))


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
    lines = [f"{index}. {label}", f"   狀態：{candidate.status}"]
    if candidate.reasons:
        lines.append(f"   原因：{'、'.join(candidate.reasons)}")
    if candidate.follow_up:
        lines.append(f"   待確認：{'、'.join(candidate.follow_up)}")
    return lines


def _format_stock_label(item: SignalItem) -> str:
    code = stock_code_for_item(item)
    company = company_name_for_item(item)
    percent = change_percent(item)
    prefix_parts = [part for part in (code, company) if part]
    prefix = " ".join(prefix_parts).strip() or item.title.strip()
    return f"{prefix} {percent:+.2f}%"
