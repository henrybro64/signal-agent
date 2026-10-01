"""Run the Taiwan market assistant research digest during market hours."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from zoneinfo import ZoneInfo

from app.config import (
    load_config,
    load_env_settings,
    validate_telegram_notifier_secrets,
)
from app.core import SignalItem, upsert_signal
from app.core.tw_stock_utils import (
    TwStockMarketReport,
    TwStockMoversSummary,
    build_market_reports,
    count_reported_movers,
)
from app.formatters import build_tw_market_assistant_digest
from app.scorers.tw_signal_classifier import (
    build_research_candidates,
    find_watchlist_hits,
    summarize_theme_activity,
)
from app.sources import build_tw_stock_mover_sources_for_names

from . import market_task_support as market_support
from .base import BaseTask


@dataclass(slots=True)
class TwMarketAssistantSummary:
    """Collected counts for one market-assistant task run."""

    stock_summary: TwStockMoversSummary
    watchlist_hits: list[SignalItem]
    research_candidate_count: int = 0
    snapshot_saved: bool = False


class TwMarketAssistantTask(BaseTask):
    """Fetch Taiwan movers and send a research-oriented Telegram digest."""

    name: ClassVar[str] = "tw_market_assistant"

    def run(self) -> None:
        db_path = market_support.resolve_task_db_path()
        stock_summary = TwStockMoversSummary()
        notification = market_support.NotificationOutcome(status="not_applicable")
        market_reports: list[TwStockMarketReport] = []
        watchlist_hits: list[SignalItem] = []
        research_candidate_count = 0
        snapshot_saved = False

        self.begin_task_run(db_path=db_path)
        self.emit_log(
            "task_started",
            message="Taiwan market assistant task started",
            db_path=str(db_path),
        )

        try:
            config = load_config(self.config_path)
            task_config = config.tasks.tw_market_assistant
            if not task_config.enabled:
                raise ValueError("Task 'tw_market_assistant' is disabled in config")

            env = load_env_settings()
            if (
                task_config.market_hours_only
                and market_support.skip_outside_tw_market_hours(
                    self,
                    db_path=db_path,
                    task_name=self.name,
                    skip_log_message="Taiwan market assistant task skipped",
                )
            ):
                return

            sources = build_tw_stock_mover_sources_for_names(
                config,
                task_config.sources,
            )
            if not sources:
                raise ValueError("No Taiwan stock-mover sources are configured.")

            stock_summary = market_support.collect_tw_stock_mover_summary(
                self,
                sources=sources,
                db_path=db_path,
            )
            # Fail before Telegram so missing secrets / send errors cannot mask
            # an all-sources-failed outcome.
            if stock_summary.successful_sources == 0:
                failure_summary = "; ".join(
                    f"{source_name}: {message}"
                    for source_name, message in stock_summary.failed_sources
                )
                raise ValueError(
                    "All configured Taiwan stock-mover sources failed: "
                    f"{failure_summary}"
                )

            market_reports = build_market_reports(
                stock_summary.stock_items,
                limit=task_config.top_n,
            )
            watchlist_hits = find_watchlist_hits(
                stock_summary.stock_items,
                _resolved_assistant_watchlist(config),
            )
            summary_bullets = summarize_theme_activity(
                market_reports,
                config.investment.theme_mappings,
            )
            research_candidates = build_research_candidates(
                market_reports=market_reports,
                watchlist_hits=watchlist_hits,
                theme_mappings=config.investment.theme_mappings,
            )
            research_candidate_count = len(research_candidates)

            digest = build_tw_market_assistant_digest(
                summary=stock_summary,
                market_reports=market_reports,
                summary_bullets=summary_bullets,
                watchlist_hits=watchlist_hits,
                research_candidates=research_candidates,
                top_n=task_config.top_n,
                timezone_name=task_config.timezone,
                notify_language=task_config.notify_language,
            )
            snapshot_saved = _persist_assistant_outputs(
                db_path=db_path,
                task_config=task_config,
                market_reports=market_reports,
                watchlist_hits=watchlist_hits,
                research_candidates=research_candidates,
                digest=digest,
            )

            print(
                "\n".join(
                    _build_console_summary(
                        config_path=self.config_path,
                        db_path=db_path,
                        stock_summary=stock_summary,
                        market_reports=market_reports,
                        watchlist_hits=watchlist_hits,
                        research_candidate_count=research_candidate_count,
                        top_n=task_config.top_n,
                        snapshot_saved=snapshot_saved,
                    )
                )
            )

            notification = market_support.send_optional_telegram_digest(
                self,
                digest=digest,
                config=config,
                env=env,
                db_path=db_path,
                notified_count=research_candidate_count,
                empty_status="skipped_no_items",
                empty_message="telegram digest skipped: no market data to report",
                validate_secrets=validate_telegram_notifier_secrets,
            )

            self.emit_log(
                "task_completed",
                message="Taiwan market assistant task completed",
                db_path=str(db_path),
                fetched_count=stock_summary.fetched_count,
                new_count=stock_summary.new_count,
                duplicate_count=stock_summary.duplicate_count,
                skipped_count=(
                    stock_summary.duplicate_count
                    + stock_summary.ignored_non_stock_count
                ),
                notified_count=notification.notified_count,
                notification_status=notification.status,
                successful_source_count=stock_summary.successful_sources,
                failed_source_count=len(stock_summary.failed_sources),
                watchlist_hit_count=len(watchlist_hits),
                research_candidate_count=research_candidate_count,
                snapshot_saved=snapshot_saved,
                reported_count=count_reported_movers(market_reports),
            )
            self.end_task_run(
                db_path=db_path,
                status="success",
                fetched_count=stock_summary.fetched_count,
                new_count=stock_summary.new_count,
                skipped_count=(
                    stock_summary.duplicate_count
                    + stock_summary.ignored_non_stock_count
                ),
                notified_count=notification.notified_count,
            )
        except Exception as exc:
            self.end_task_run(
                db_path=db_path,
                status="failed",
                fetched_count=stock_summary.fetched_count,
                new_count=stock_summary.new_count,
                skipped_count=(
                    stock_summary.duplicate_count
                    + stock_summary.ignored_non_stock_count
                ),
                notified_count=notification.notified_count,
                error_message=str(exc),
            )
            self.emit_log(
                "task_failed",
                message="Taiwan market assistant task failed",
                level=logging.ERROR,
                exc_info=exc,
                db_path=str(db_path),
                error=str(exc),
            )
            raise


def _resolved_assistant_watchlist(config) -> list[str]:
    """Prefer investment.watchlist; fall back to stock_watchlist symbol codes."""

    codes = [
        str(code).strip()
        for code in config.investment.watchlist
        if str(code).strip()
    ]
    if codes:
        return codes
    return [
        symbol.code.strip()
        for symbol in config.tasks.stock_watchlist.symbols
        if symbol.code.strip()
    ]


def _persist_assistant_outputs(
    *,
    db_path: Path,
    task_config,
    market_reports: list[TwStockMarketReport],
    watchlist_hits: list[SignalItem],
    research_candidates,
    digest: str,
) -> bool:
    if not digest:
        return False

    now = datetime.now(ZoneInfo(task_config.timezone))
    snapshot_key = now.strftime("%Y%m%d-%H%M")
    upsert_signal(
        SignalItem(
            source="tw_market_assistant",
            item_type="tw_market_assistant_snapshot",
            title=f"台股投資助理快報 {now.strftime('%Y-%m-%d %H:%M')}",
            observed_at=now,
            external_id=f"tw_market_assistant-{snapshot_key}",
            content=digest,
            metadata={
                "watchlist_hit_codes": [
                    item.metadata.get("stock_code")
                    for item in watchlist_hits
                    if item.metadata.get("stock_code")
                ],
                "research_candidate_count": len(research_candidates),
                "reported_mover_count": count_reported_movers(market_reports),
            },
        ),
        db_path=db_path,
    )

    for candidate in research_candidates:
        stock_code = candidate.item.metadata.get("stock_code")
        upsert_signal(
            SignalItem(
                source="tw_market_assistant",
                item_type="tw_research_candidate",
                title=f"{stock_code or candidate.item.title} 研究候選",
                observed_at=now,
                external_id=(
                    f"tw_research_candidate-{snapshot_key}-"
                    f"{stock_code or candidate.item.external_id or 'unknown'}"
                ),
                content=candidate.item.content,
                metadata={
                    "status": candidate.status,
                    "themes": candidate.themes,
                    "reasons": candidate.reasons,
                    "follow_up": candidate.follow_up,
                    "stock_code": stock_code,
                    "change_percent": candidate.item.metadata.get("change_percent"),
                    "market": candidate.item.metadata.get("market"),
                },
            ),
            db_path=db_path,
        )

    return True


def _build_console_summary(
    *,
    config_path: str,
    db_path: Path,
    stock_summary: TwStockMoversSummary,
    market_reports: list[TwStockMarketReport],
    watchlist_hits: list[SignalItem],
    research_candidate_count: int,
    top_n: int,
    snapshot_saved: bool,
) -> list[str]:
    reported_count = count_reported_movers(market_reports)
    lines = [
        f"tw_market_assistant task executed using {config_path}:",
        (
            f"fetched {stock_summary.fetched_count} stock mover(s) from "
            f"{stock_summary.successful_sources} successful source(s)"
        ),
        (
            f"persisted {stock_summary.new_count} new / "
            f"{stock_summary.duplicate_count} duplicate to {db_path}"
        ),
        (
            f"reporting {reported_count} ranked mover(s) "
            f"(limit={top_n} each), watchlist hits={len(watchlist_hits)}, "
            f"research candidates={research_candidate_count}"
        ),
    ]
    if snapshot_saved:
        lines.append("saved market-assistant snapshot and research candidates")
    for source_name, message in stock_summary.failed_sources:
        lines.append(f"source failed: {source_name}: {message}")
    return lines
