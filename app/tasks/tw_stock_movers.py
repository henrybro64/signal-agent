"""Run the Taiwan stock-mover pipeline during market hours."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Final
from zoneinfo import ZoneInfo

from app.config import (
    load_config,
    load_env_settings,
    validate_telegram_notifier_secrets,
)
from app.core import (
    SignalItem,
    upsert_signal,
)
from app.sources import build_tw_stock_mover_sources

from .base import BaseTask
from .market_task_support import (
    NotificationOutcome,
    complete_tw_stock_source_run,
    fetch_tw_stock_mover_source,
    resolve_task_db_path,
    send_optional_telegram_digest,
    skip_outside_tw_market_hours,
    source_warning_label,
)
from .market_task_support import (
    change_percent as item_change_percent,
)
from .market_task_support import (
    company_name as item_company_name,
)
from .market_task_support import (
    stock_code as item_stock_code,
)

_MARKET_SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("twse", "TWSE listed (上市)"),
    ("tpex", "TPEx OTC (上櫃)"),
)
_MARKET_TELEGRAM_LABELS: Final[dict[str, str]] = {
    "twse": "上市",
    "tpex": "上櫃",
}


@dataclass(slots=True)
class TwStockMoversSummary:
    """Collected counts and movers for one stock-mover task run."""

    successful_sources: int = 0
    fetched_count: int = 0
    new_count: int = 0
    duplicate_count: int = 0
    stock_items: list[SignalItem] = field(default_factory=list)
    failed_sources: list[tuple[str, str]] = field(default_factory=list)
    ignored_non_stock_count: int = 0


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


class TwStockMoversTask(BaseTask):
    """Fetch, persist, and optionally report Taiwan stock movers."""

    name: ClassVar[str] = "tw_stock_movers"

    def run(self) -> None:
        db_path = resolve_task_db_path()
        summary = TwStockMoversSummary()
        notification = NotificationOutcome(status="not_applicable")
        market_reports: list[TwStockMarketReport] = []

        self.begin_task_run(db_path=db_path)
        self.emit_log(
            "task_started",
            message="Taiwan stock movers task started",
            db_path=str(db_path),
        )

        try:
            config = load_config(self.config_path)
            if not config.tasks.tw_stock_movers.enabled:
                raise ValueError("Task 'tw_stock_movers' is disabled in config")

            env = load_env_settings()
            if skip_outside_tw_market_hours(
                self,
                db_path=db_path,
                task_name=self.name,
                skip_log_message="Taiwan stock movers task skipped",
            ):
                return

            sources = build_tw_stock_mover_sources(config)
            if not sources:
                raise ValueError("No Taiwan stock-mover sources are configured.")
            for source in sources:
                fetch = fetch_tw_stock_mover_source(
                    self,
                    source=source,
                    db_path=db_path,
                    failed_sources=summary.failed_sources,
                )
                if fetch is None:
                    continue

                ignored_count = len(fetch.fetched_items) - len(fetch.stock_items)
                summary.ignored_non_stock_count += ignored_count
                summary.fetched_count += len(fetch.stock_items)

                source_inserted = 0
                source_updated = 0
                for item in fetch.stock_items:
                    result = upsert_signal(item, db_path=db_path)
                    if result.action == "inserted":
                        source_inserted += 1
                        summary.new_count += 1
                    else:
                        source_updated += 1
                        summary.duplicate_count += 1
                    summary.stock_items.append(item)

                complete_tw_stock_source_run(
                    self,
                    source_name=source.name,
                    fetch=fetch,
                    db_path=db_path,
                    inserted_count=source_inserted,
                    updated_count=source_updated,
                    ignored_count=ignored_count,
                    new_count=source_inserted,
                    duplicate_count=source_updated,
                    skipped_count=source_updated + ignored_count,
                )
                summary.successful_sources += 1

            market_reports = _build_market_reports(
                summary.stock_items,
                limit=config.tasks.tw_stock_movers.top_n,
            )
            print(
                "\n".join(
                    _build_console_summary(
                        config_path=self.config_path,
                        db_path=db_path,
                        summary=summary,
                        market_reports=market_reports,
                        top_n=config.tasks.tw_stock_movers.top_n,
                    )
                )
            )

            notification = self._maybe_send_telegram_digest(
                config=config,
                env=env,
                summary=summary,
                market_reports=market_reports,
                top_n=config.tasks.tw_stock_movers.top_n,
                timezone_name=config.tasks.tw_stock_movers.timezone,
                db_path=db_path,
            )
            if summary.successful_sources == 0:
                self.emit_log(
                    "task_summary",
                    message="Taiwan stock movers task summary",
                    db_path=str(db_path),
                    task_status="failed",
                    fetched_count=summary.fetched_count,
                    new_count=summary.new_count,
                    duplicate_count=summary.duplicate_count,
                    skipped_count=(
                        summary.duplicate_count + summary.ignored_non_stock_count
                    ),
                    notified_count=notification.notified_count,
                    notification_status=notification.status,
                    successful_source_count=summary.successful_sources,
                    failed_source_count=len(summary.failed_sources),
                    ignored_count=summary.ignored_non_stock_count,
                    reported_count=_count_reported_movers(market_reports),
                    active_market_count=sum(
                        1
                        for report in market_reports
                        if report.reported_count > 0
                    ),
                )
                failure_summary = "; ".join(
                    f"{source_name}: {message}"
                    for source_name, message in summary.failed_sources
                )
                raise ValueError(
                    "All configured Taiwan stock-mover sources failed: "
                    f"{failure_summary}"
                )
            self.emit_log(
                "task_completed",
                message="Taiwan stock movers task completed",
                db_path=str(db_path),
                fetched_count=summary.fetched_count,
                new_count=summary.new_count,
                duplicate_count=summary.duplicate_count,
                skipped_count=(
                    summary.duplicate_count + summary.ignored_non_stock_count
                ),
                notified_count=notification.notified_count,
                notification_status=notification.status,
                successful_source_count=summary.successful_sources,
                failed_source_count=len(summary.failed_sources),
                ignored_count=summary.ignored_non_stock_count,
                reported_count=_count_reported_movers(market_reports),
                active_market_count=sum(
                    1 for report in market_reports if report.reported_count > 0
                ),
            )
            self.end_task_run(
                db_path=db_path,
                status="success",
                fetched_count=summary.fetched_count,
                new_count=summary.new_count,
                skipped_count=(
                    summary.duplicate_count + summary.ignored_non_stock_count
                ),
                notified_count=notification.notified_count,
            )
        except Exception as exc:
            self.end_task_run(
                db_path=db_path,
                status="failed",
                fetched_count=summary.fetched_count,
                new_count=summary.new_count,
                skipped_count=(
                    summary.duplicate_count + summary.ignored_non_stock_count
                ),
                notified_count=notification.notified_count,
                error_message=str(exc),
            )
            self.emit_log(
                "task_failed",
                message="Taiwan stock movers task failed",
                level=logging.ERROR,
                exc_info=exc,
                db_path=str(db_path),
                error=str(exc),
            )
            raise

    def _maybe_send_telegram_digest(
        self,
        *,
        config,
        env,
        summary: TwStockMoversSummary,
        market_reports: list[TwStockMarketReport],
        top_n: int,
        timezone_name: str,
        db_path: Path,
    ) -> NotificationOutcome:
        reported_count = _count_reported_movers(market_reports)
        digest = _build_telegram_digest(
            summary=summary,
            market_reports=market_reports,
            top_n=top_n,
            timezone_name=timezone_name,
            run_status=(
                "failed" if summary.successful_sources == 0 else "success"
            ),
            notified_count=reported_count,
        )
        return send_optional_telegram_digest(
            self,
            digest=digest,
            config=config,
            env=env,
            db_path=db_path,
            notified_count=reported_count,
            empty_status="skipped_no_items",
            empty_message="telegram digest skipped: no stock movers to report",
            validate_secrets=validate_telegram_notifier_secrets,
        )


def _build_console_summary(
    *,
    config_path: str,
    db_path: Path,
    summary: TwStockMoversSummary,
    market_reports: list[TwStockMarketReport],
    top_n: int,
) -> list[str]:
    reported_count = _count_reported_movers(market_reports)
    active_markets = sum(1 for report in market_reports if report.reported_count > 0)
    lines = [
        f"tw_stock_movers task executed using {config_path}:",
        (
            f"fetched {summary.fetched_count} stock mover(s) from "
            f"{summary.successful_sources} successful source(s):"
        ),
        (
            f"persisted {summary.new_count} new / {summary.duplicate_count} "
            f"duplicate to {db_path}"
        ),
        (
            f"reporting {reported_count} ranked mover(s) across "
            f"{active_markets} market(s) (limit={top_n} each)"
        ),
    ]
    if summary.ignored_non_stock_count:
        lines.append(
            f"ignored {summary.ignored_non_stock_count} non-stock signal(s)"
        )
    for source_name, message in summary.failed_sources:
        lines.append(f"source failed: {source_name}: {message}")
    for report in market_reports:
        if report.reported_count == 0:
            continue
        lines.append(
            f"{report.title}: {len(report.gainers)} gainer(s) / "
            f"{len(report.losers)} loser(s)"
        )
        if report.gainers:
            lines.append("Top gainers")
            lines.extend(
                f"- {_render_mover_line(item)}" for item in report.gainers
            )
        if report.losers:
            lines.append("Top losers")
            lines.extend(
                f"- {_render_mover_line(item)}" for item in report.losers
            )
    return lines


def _build_telegram_digest(
    *,
    summary: TwStockMoversSummary,
    market_reports: list[TwStockMarketReport],
    top_n: int,
    timezone_name: str,
    run_status: str,
    notified_count: int,
) -> str:
    reported_count = _count_reported_movers(market_reports)
    has_useful_output = reported_count > 0 or bool(summary.failed_sources)
    if not has_useful_output:
        return ""

    lines = [_build_telegram_heading(timezone_name)]

    for report in market_reports:
        if report.reported_count == 0:
            continue
        if report.gainers:
            lines.append("")
            lines.append(
                _build_telegram_section_title(
                    market_key=report.market_key,
                    top_n=top_n,
                    direction="gainers",
                )
            )
            for index, item in enumerate(report.gainers, start=1):
                lines.append(_render_digest_ranked_line(index, item))

        if report.losers:
            lines.append("")
            lines.append(
                _build_telegram_section_title(
                    market_key=report.market_key,
                    top_n=top_n,
                    direction="losers",
                )
            )
            for index, item in enumerate(report.losers, start=1):
                lines.append(_render_digest_ranked_line(index, item))

    if summary.failed_sources:
        lines.append("")
        lines.append("資料來源異常")
        for source_name, message in summary.failed_sources:
            lines.append(f"- {source_warning_label(source_name)}：{message}")

    lines.append("")
    lines.append(
        "執行摘要："
        f"狀態={'成功' if run_status == 'success' else '失敗'}，"
        f"抓取={summary.fetched_count}，"
        f"新增={summary.new_count}，"
        f"略過={summary.duplicate_count + summary.ignored_non_stock_count}，"
        f"通知={notified_count}"
    )

    return "\n".join(lines)


def _now_in_timezone(timezone_name: str) -> datetime:
    return datetime.now(ZoneInfo(timezone_name))


def _build_telegram_heading(timezone_name: str) -> str:
    timestamp = _now_in_timezone(timezone_name).strftime("%Y-%m-%d %H:%M")
    return f"台股漲跌幅快報 {timestamp}"


def _build_telegram_section_title(
    *,
    market_key: str,
    top_n: int,
    direction: str,
) -> str:
    market_label = _MARKET_TELEGRAM_LABELS.get(market_key, market_key.upper())
    direction_label = "漲幅" if direction == "gainers" else "跌幅"
    return f"{market_label}前{top_n}{direction_label}"


def _build_market_reports(
    items: list[SignalItem],
    *,
    limit: int,
) -> list[TwStockMarketReport]:
    return [
        TwStockMarketReport(
            market_key=market_key,
            title=title,
            gainers=_select_top_gainers(
                items,
                limit=limit,
                market_key=market_key,
            ),
            losers=_select_top_losers(
                items,
                limit=limit,
                market_key=market_key,
            ),
        )
        for market_key, title in _MARKET_SECTIONS
    ]


def _count_reported_movers(market_reports: list[TwStockMarketReport]) -> int:
    return sum(report.reported_count for report in market_reports)


def _select_top_gainers(
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
        if item_change_percent(item) > 0
        and (market_key is None or _market_key(item) == market_key)
    ]
    return sorted(
        gainers,
        key=lambda item: (item_change_percent(item), item.observed_at),
        reverse=True,
    )[:limit]


def _select_top_losers(
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
        if item_change_percent(item) < 0
        and (market_key is None or _market_key(item) == market_key)
    ]
    return sorted(
        losers,
        key=lambda item: (item_change_percent(item), -item.observed_at.timestamp()),
    )[:limit]


def _market_key(item: SignalItem) -> str:
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


def _render_mover_line(item: SignalItem) -> str:
    code = item_stock_code(item)
    company = item_company_name(item)
    pct = item_change_percent(item)
    title = item.title.strip()

    prefix_parts = [part for part in (code, company) if part]
    prefix = " ".join(prefix_parts)
    if prefix:
        prefix += " "
    return f"{prefix}{pct:+.1f}% | {title}"


def _render_digest_ranked_line(index: int, item: SignalItem) -> str:
    code = item_stock_code(item)
    company = item_company_name(item)
    pct = item_change_percent(item)

    prefix_parts = [part for part in (code, company) if part]
    prefix = " ".join(prefix_parts).strip()
    if not prefix:
        prefix = item.title.strip()
    return f"{index}. {prefix} {pct:+.2f}%"


