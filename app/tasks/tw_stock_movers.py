"""Run the Taiwan stock-mover pipeline during market hours."""

from __future__ import annotations

import logging
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
)
from app.core.tw_stock_utils import (
    TwStockMarketReport,
    TwStockMoversSummary,
    build_market_reports,
    company_name_for_item,
    count_reported_movers,
    render_digest_ranked_line,
    stock_code_for_item,
)
from app.core.tw_stock_utils import (
    change_percent as item_change_percent,
)
from app.sources import build_tw_stock_mover_sources

from .base import BaseTask
from .market_task_support import (
    NotificationOutcome,
    collect_tw_stock_mover_summary,
    resolve_task_db_path,
    send_optional_telegram_digest,
    skip_outside_tw_market_hours,
    source_warning_label,
)

_MARKET_TELEGRAM_LABELS: Final[dict[str, str]] = {
    "twse": "上市",
    "tpex": "上櫃",
}


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
            summary = collect_tw_stock_mover_summary(
                self,
                sources=sources,
                db_path=db_path,
            )

            market_reports = build_market_reports(
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
                    reported_count=count_reported_movers(market_reports),
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
                reported_count=count_reported_movers(market_reports),
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
        reported_count = count_reported_movers(market_reports)
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
    reported_count = count_reported_movers(market_reports)
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
    reported_count = count_reported_movers(market_reports)
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
                lines.append(render_digest_ranked_line(index, item))

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
                lines.append(render_digest_ranked_line(index, item))

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


def _render_mover_line(item: SignalItem) -> str:
    code = stock_code_for_item(item)
    company = company_name_for_item(item)
    pct = item_change_percent(item)
    title = item.title.strip()

    prefix_parts = [part for part in (code, company) if part]
    prefix = " ".join(prefix_parts)
    if prefix:
        prefix += " "
    return f"{prefix}{pct:+.1f}% | {title}"


