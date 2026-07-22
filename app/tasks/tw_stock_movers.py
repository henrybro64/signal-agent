"""Run the Taiwan stock-mover pipeline during market hours."""

from __future__ import annotations

import logging
import os
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
    TW_MARKET_SKIP_CALENDAR_UNAVAILABLE,
    TW_MARKET_SKIP_HOLIDAY,
    SignalItem,
    finish_signal_run,
    start_signal_run,
    tw_market_skip_reason,
    upsert_signal,
)
from app.notifiers import build_telegram_notifier
from app.sources import build_tw_stock_mover_sources

from .base import BaseTask
from .fetch import DB_PATH_ENV_VAR, DEFAULT_DB_PATH

_MARKET_SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("twse", "TWSE listed (上市)"),
    ("tpex", "TPEx OTC (上櫃)"),
)
_MARKET_TELEGRAM_LABELS: Final[dict[str, str]] = {
    "twse": "上市",
    "tpex": "上櫃",
}
_SOURCE_WARNING_LABELS: Final[dict[str, str]] = {
    "twse": "上市資料",
    "tpex": "上櫃資料",
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


@dataclass(slots=True, frozen=True)
class NotificationOutcome:
    """Summarize the Telegram notification phase for structured logs."""

    status: str
    notified_count: int = 0


class TwStockMoversTask(BaseTask):
    """Fetch, persist, and optionally report Taiwan stock movers."""

    name: ClassVar[str] = "tw_stock_movers"

    def run(self) -> None:
        db_path = _resolve_db_path()
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
            skip_reason = tw_market_skip_reason()
            if skip_reason is not None:
                if skip_reason == TW_MARKET_SKIP_HOLIDAY:
                    skip_label = "Taiwan market holiday"
                elif skip_reason == TW_MARKET_SKIP_CALENDAR_UNAVAILABLE:
                    skip_label = "Taiwan market calendar unavailable"
                else:
                    skip_label = "Taiwan market is closed"
                self.emit_log(
                    "task_skipped",
                    message="Taiwan stock movers task skipped",
                    db_path=str(db_path),
                    skip_reason=skip_reason,
                )
                print(
                    "tw_stock_movers task skipped using "
                    f"{self.config_path}: {skip_label}"
                )
                self.end_task_run(
                    db_path=db_path,
                    status="skipped",
                    notified_count=0,
                    error_message=skip_reason,
                )
                return

            sources = build_tw_stock_mover_sources(config)
            if not sources:
                raise ValueError("No Taiwan stock-mover sources are configured.")
            for source in sources:
                run_id = start_signal_run(source.name, db_path=db_path)
                self.emit_log(
                    "source_run_started",
                    message="Source run started",
                    db_path=str(db_path),
                    source_name=source.name,
                    run_id=run_id,
                )
                try:
                    fetched_items = source.fetch()
                    stock_items = [
                        item
                        for item in fetched_items
                        if item.item_type == "tw_stock_mover"
                    ]
                    ignored_count = len(fetched_items) - len(stock_items)
                    summary.ignored_non_stock_count += ignored_count
                    summary.fetched_count += len(stock_items)

                    source_inserted = 0
                    source_updated = 0
                    for item in stock_items:
                        result = upsert_signal(item, db_path=db_path)
                        if result.action == "inserted":
                            source_inserted += 1
                            summary.new_count += 1
                        else:
                            source_updated += 1
                            summary.duplicate_count += 1
                        summary.stock_items.append(item)

                    finish_signal_run(
                        run_id,
                        status="completed",
                        fetched_count=len(stock_items),
                        inserted_count=source_inserted,
                        updated_count=source_updated,
                        db_path=db_path,
                    )
                    self.emit_log(
                        "source_run_completed",
                        message="Source run completed",
                        db_path=str(db_path),
                        source_name=source.name,
                        run_id=run_id,
                        fetched_count=len(fetched_items),
                        accepted_count=len(stock_items),
                        ignored_count=ignored_count,
                        new_count=source_inserted,
                        duplicate_count=source_updated,
                        skipped_count=source_updated + ignored_count,
                    )
                    summary.successful_sources += 1
                except Exception as exc:
                    finish_signal_run(
                        run_id,
                        status="failed",
                        error=str(exc),
                        db_path=db_path,
                    )
                    summary.failed_sources.append((source.name, str(exc)))
                    self.emit_log(
                        "source_run_failed",
                        message="Source run failed",
                        level=logging.ERROR,
                        exc_info=exc,
                        db_path=str(db_path),
                        source_name=source.name,
                        run_id=run_id,
                        error=str(exc),
                    )

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
        if not digest:
            self.emit_log(
                "notification_skipped",
                message="Telegram digest skipped",
                db_path=str(db_path),
                notifier="telegram",
                notification_status="skipped_no_items",
                notified_count=0,
            )
            print("telegram digest skipped: no stock movers to report")
            return NotificationOutcome(status="skipped_no_items")

        if not config.notifiers.telegram.enabled:
            self.emit_log(
                "notification_skipped",
                message="Telegram digest skipped",
                db_path=str(db_path),
                notifier="telegram",
                notification_status="skipped_notifier_disabled",
                notified_count=0,
            )
            print("telegram digest skipped: notifier disabled in config")
            return NotificationOutcome(status="skipped_notifier_disabled")

        validate_telegram_notifier_secrets(
            telegram_enabled=True,
            env=env,
        )
        notifier = build_telegram_notifier(env=env)
        try:
            notifier.send_message(digest)
        except Exception as exc:
            self.emit_log(
                "notification_failed",
                message="Telegram digest failed",
                level=logging.ERROR,
                exc_info=exc,
                db_path=str(db_path),
                notifier="telegram",
                notification_status="failed",
                attempted_notified_count=reported_count,
                error=str(exc),
            )
            raise
        self.emit_log(
            "notification_sent",
            message="Telegram digest sent",
            db_path=str(db_path),
            notifier="telegram",
            notification_status="sent",
            notified_count=reported_count,
        )
        print("telegram digest sent")
        return NotificationOutcome(
            status="sent",
            notified_count=reported_count,
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
            lines.append(f"- {_source_warning_label(source_name)}：{message}")

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


def _source_warning_label(source_name: str) -> str:
    return _SOURCE_WARNING_LABELS.get(source_name, source_name)


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
        if _change_percent(item) > 0
        and (market_key is None or _market_key(item) == market_key)
    ]
    return sorted(
        gainers,
        key=lambda item: (_change_percent(item), item.observed_at),
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
        if _change_percent(item) < 0
        and (market_key is None or _market_key(item) == market_key)
    ]
    return sorted(
        losers,
        key=lambda item: (_change_percent(item), -item.observed_at.timestamp()),
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
    stock_code = _stock_code(item)
    company = _company_name(item)
    change_percent = _change_percent(item)
    title = item.title.strip()

    prefix_parts = [part for part in (stock_code, company) if part]
    prefix = " ".join(prefix_parts)
    if prefix:
        prefix += " "
    return f"{prefix}{change_percent:+.1f}% | {title}"


def _render_digest_ranked_line(index: int, item: SignalItem) -> str:
    stock_code = _stock_code(item)
    company = _company_name(item)
    change_percent = _change_percent(item)

    prefix_parts = [part for part in (stock_code, company) if part]
    prefix = " ".join(prefix_parts).strip()
    if not prefix:
        prefix = item.title.strip()
    return f"{index}. {prefix} {change_percent:+.2f}%"


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


def _change_percent(item: SignalItem) -> float:
    change_percent = item.metadata.get("change_percent")
    if isinstance(change_percent, int | float):
        return float(change_percent)
    return 0.0


def _resolve_db_path() -> Path:
    raw_path = os.getenv(DB_PATH_ENV_VAR)
    if raw_path:
        return Path(raw_path).expanduser()
    return DEFAULT_DB_PATH
