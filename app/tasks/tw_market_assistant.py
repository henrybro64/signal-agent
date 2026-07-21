"""Run the Taiwan market assistant research digest during market hours."""

from __future__ import annotations

import logging
import os
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
from app.core import (
    SignalItem,
    finish_signal_run,
    is_tw_market_open,
    start_signal_run,
    upsert_signal,
)
from app.core.tw_stock_utils import (
    TwStockMarketReport,
    build_market_reports,
    count_reported_movers,
)
from app.formatters import build_tw_market_assistant_digest
from app.notifiers import build_telegram_notifier
from app.scorers.tw_signal_classifier import (
    build_research_candidates,
    find_watchlist_hits,
    summarize_theme_activity,
)
from app.sources import build_tw_stock_mover_sources_for_names
from app.tasks.tw_stock_movers import NotificationOutcome, TwStockMoversSummary

from .base import BaseTask
from .fetch import DB_PATH_ENV_VAR, DEFAULT_DB_PATH


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
        db_path = _resolve_db_path()
        stock_summary = TwStockMoversSummary()
        notification = NotificationOutcome(status="not_applicable")
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
            if task_config.market_hours_only and not is_tw_market_open():
                self.emit_log(
                    "task_skipped",
                    message="Taiwan market assistant task skipped",
                    db_path=str(db_path),
                    skip_reason="taiwan_market_closed",
                )
                print(
                    "tw_market_assistant task skipped using "
                    f"{self.config_path}: Taiwan market is closed"
                )
                self.end_task_run(
                    db_path=db_path,
                    status="success",
                    notified_count=0,
                )
                return

            sources = build_tw_stock_mover_sources_for_names(
                config,
                task_config.sources,
            )
            if not sources:
                raise ValueError("No Taiwan stock-mover sources are configured.")

            stock_summary = _fetch_and_persist_sources(
                sources=sources,
                db_path=db_path,
                emit_log=self.emit_log,
            )
            market_reports = build_market_reports(
                stock_summary.stock_items,
                limit=task_config.top_n,
            )
            watchlist_hits = find_watchlist_hits(
                stock_summary.stock_items,
                config.investment.watchlist,
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

            snapshot_saved = _persist_assistant_outputs(
                db_path=db_path,
                task_config=task_config,
                market_reports=market_reports,
                watchlist_hits=watchlist_hits,
                research_candidates=research_candidates,
                digest=build_tw_market_assistant_digest(
                    summary=stock_summary,
                    market_reports=market_reports,
                    summary_bullets=summary_bullets,
                    watchlist_hits=watchlist_hits,
                    research_candidates=research_candidates,
                    top_n=task_config.top_n,
                    timezone_name=task_config.timezone,
                ),
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

            notification = self._maybe_send_telegram_digest(
                config=config,
                env=env,
                stock_summary=stock_summary,
                market_reports=market_reports,
                summary_bullets=summary_bullets,
                watchlist_hits=watchlist_hits,
                research_candidates=research_candidates,
                top_n=task_config.top_n,
                timezone_name=task_config.timezone,
                db_path=db_path,
            )

            if stock_summary.successful_sources == 0:
                failure_summary = "; ".join(
                    f"{source_name}: {message}"
                    for source_name, message in stock_summary.failed_sources
                )
                raise ValueError(
                    "All configured Taiwan stock-mover sources failed: "
                    f"{failure_summary}"
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

    def _maybe_send_telegram_digest(
        self,
        *,
        config,
        env,
        stock_summary: TwStockMoversSummary,
        market_reports: list[TwStockMarketReport],
        summary_bullets: list[str],
        watchlist_hits: list[SignalItem],
        research_candidates,
        top_n: int,
        timezone_name: str,
        db_path: Path,
    ) -> NotificationOutcome:
        digest = build_tw_market_assistant_digest(
            summary=stock_summary,
            market_reports=market_reports,
            summary_bullets=summary_bullets,
            watchlist_hits=watchlist_hits,
            research_candidates=research_candidates,
            top_n=top_n,
            timezone_name=timezone_name,
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
            print("telegram digest skipped: no market data to report")
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
                attempted_notified_count=len(research_candidates),
                error=str(exc),
            )
            raise

        self.emit_log(
            "notification_sent",
            message="Telegram digest sent",
            db_path=str(db_path),
            notifier="telegram",
            notification_status="sent",
            notified_count=len(research_candidates),
        )
        print("telegram digest sent")
        return NotificationOutcome(
            status="sent",
            notified_count=len(research_candidates),
        )


def _fetch_and_persist_sources(
    *,
    sources,
    db_path: Path,
    emit_log,
) -> TwStockMoversSummary:
    summary = TwStockMoversSummary()

    for source in sources:
        run_id = start_signal_run(source.name, db_path=db_path)
        emit_log(
            "source_run_started",
            message="Source run started",
            db_path=str(db_path),
            source_name=source.name,
            run_id=run_id,
        )
        try:
            fetched_items = source.fetch()
            stock_items = [
                item for item in fetched_items if item.item_type == "tw_stock_mover"
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
            emit_log(
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
            emit_log(
                "source_run_failed",
                message="Source run failed",
                level=logging.ERROR,
                exc_info=exc,
                db_path=str(db_path),
                source_name=source.name,
                run_id=run_id,
                error=str(exc),
            )

    return summary


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


def _resolve_db_path() -> Path:
    raw_path = os.getenv(DB_PATH_ENV_VAR)
    if raw_path:
        return Path(raw_path).expanduser()
    return DEFAULT_DB_PATH
