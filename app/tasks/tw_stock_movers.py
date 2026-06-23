"""Run the Taiwan stock-mover pipeline during market hours."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from app.config import (
    load_config,
    load_env_settings,
    validate_telegram_notifier_secrets,
)
from app.core import (
    SignalItem,
    finish_signal_run,
    init_db,
    is_tw_market_open,
    start_signal_run,
    upsert_signal,
)
from app.notifiers import build_telegram_notifier
from app.sources import build_tw_stock_mover_sources

from .base import BaseTask
from .fetch import DB_PATH_ENV_VAR, DEFAULT_DB_PATH


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


class TwStockMoversTask(BaseTask):
    """Fetch, persist, and optionally report Taiwan stock movers."""

    name: ClassVar[str] = "tw_stock_movers"

    def run(self) -> None:
        config = load_config(self.config_path)
        if not config.tasks.tw_stock_movers.enabled:
            raise ValueError("Task 'tw_stock_movers' is disabled in config")

        env = load_env_settings()
        if not is_tw_market_open():
            print(
                "tw_stock_movers task skipped using "
                f"{self.config_path}: Taiwan market is closed"
            )
            return

        sources = build_tw_stock_mover_sources(config)
        if not sources:
            raise ValueError("No Taiwan stock-mover sources are configured.")

        db_path = _resolve_db_path()
        init_db(db_path)

        summary = TwStockMoversSummary()
        for source in sources:
            run_id = start_signal_run(source.name, db_path=db_path)
            try:
                fetched_items = source.fetch()
                stock_items = [
                    item
                    for item in fetched_items
                    if item.item_type == "tw_stock_mover"
                ]
                summary.ignored_non_stock_count += len(fetched_items) - len(
                    stock_items
                )
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
                summary.successful_sources += 1
            except Exception as exc:
                finish_signal_run(
                    run_id,
                    status="failed",
                    error=str(exc),
                    db_path=db_path,
                )
                summary.failed_sources.append((source.name, str(exc)))

        top_gainers = _select_top_gainers(
            summary.stock_items,
            limit=config.tasks.tw_stock_movers.top_n,
        )
        top_losers = _select_top_losers(
            summary.stock_items,
            limit=config.tasks.tw_stock_movers.top_n,
        )
        print(
            "\n".join(
                _build_console_summary(
                    config_path=self.config_path,
                    db_path=db_path,
                    summary=summary,
                    top_gainers=top_gainers,
                    top_losers=top_losers,
                    top_n=config.tasks.tw_stock_movers.top_n,
                )
            )
        )

        self._maybe_send_telegram_digest(
            config=config,
            env=env,
            summary=summary,
            top_gainers=top_gainers,
            top_losers=top_losers,
        )

        if summary.successful_sources == 0:
            failure_summary = "; ".join(
                f"{source_name}: {message}"
                for source_name, message in summary.failed_sources
            )
            raise ValueError(
                "All configured Taiwan stock-mover sources failed: "
                f"{failure_summary}"
            )

    def _maybe_send_telegram_digest(
        self,
        *,
        config,
        env,
        summary: TwStockMoversSummary,
        top_gainers: list[SignalItem],
        top_losers: list[SignalItem],
    ) -> None:
        digest = _build_telegram_digest(
            summary=summary,
            top_gainers=top_gainers,
            top_losers=top_losers,
        )
        if not digest:
            print("telegram digest skipped: no stock movers to report")
            return

        if not config.notifiers.telegram.enabled:
            print("telegram digest skipped: notifier disabled in config")
            return

        validate_telegram_notifier_secrets(
            telegram_enabled=True,
            env=env,
        )
        notifier = build_telegram_notifier(env=env)
        notifier.send_message(digest)
        print("telegram digest sent")


def _build_console_summary(
    *,
    config_path: str,
    db_path: Path,
    summary: TwStockMoversSummary,
    top_gainers: list[SignalItem],
    top_losers: list[SignalItem],
    top_n: int,
) -> list[str]:
    reported_count = len(top_gainers) + len(top_losers)
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
            f"reporting {reported_count} ranked mover(s): "
            f"{len(top_gainers)} gainer(s) / {len(top_losers)} loser(s) "
            f"(limit={top_n} each)"
        ),
    ]
    if summary.ignored_non_stock_count:
        lines.append(
            f"ignored {summary.ignored_non_stock_count} non-stock signal(s)"
        )
    for source_name, message in summary.failed_sources:
        lines.append(f"source failed: {source_name}: {message}")
    if top_gainers:
        lines.append("Top gainers")
        lines.extend(f"- {_render_mover_line(item)}" for item in top_gainers)
    if top_losers:
        lines.append("Top losers")
        lines.extend(f"- {_render_mover_line(item)}" for item in top_losers)
    return lines


def _build_telegram_digest(
    *,
    summary: TwStockMoversSummary,
    top_gainers: list[SignalItem],
    top_losers: list[SignalItem],
) -> str:
    reported_count = len(top_gainers) + len(top_losers)
    has_useful_output = reported_count > 0 or bool(summary.failed_sources)
    if not has_useful_output:
        return ""

    lines = [
        "signal-agent tw stock movers digest",
        (
            "counts: "
            f"fetched={summary.fetched_count}, "
            f"new={summary.new_count}, "
            f"duplicates={summary.duplicate_count}, "
            f"reported={reported_count}"
        ),
    ]

    if top_gainers:
        lines.append("")
        lines.append("Top gainers")
        for item in top_gainers:
            lines.append(f"- {_render_mover_line(item)}")
            if item.content:
                lines.append(f"  {item.content}")
            if item.url:
                lines.append(f"  {item.url}")

    if top_losers:
        lines.append("")
        lines.append("Top losers")
        for item in top_losers:
            lines.append(f"- {_render_mover_line(item)}")
            if item.content:
                lines.append(f"  {item.content}")
            if item.url:
                lines.append(f"  {item.url}")

    if summary.failed_sources:
        lines.append("")
        lines.append("Source failures")
        for source_name, message in summary.failed_sources:
            lines.append(f"- {source_name}: {message}")

    return "\n".join(lines)


def _select_top_gainers(items: list[SignalItem], *, limit: int) -> list[SignalItem]:
    if limit <= 0 or not items:
        return []
    gainers = [
        item
        for item in items
        if _change_percent(item) > 0
    ]
    return sorted(
        gainers,
        key=lambda item: (_change_percent(item), item.observed_at),
        reverse=True,
    )[:limit]


def _select_top_losers(items: list[SignalItem], *, limit: int) -> list[SignalItem]:
    if limit <= 0 or not items:
        return []
    losers = [
        item
        for item in items
        if _change_percent(item) < 0
    ]
    return sorted(
        losers,
        key=lambda item: (_change_percent(item), -item.observed_at.timestamp()),
    )[:limit]


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
