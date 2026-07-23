"""Run personal Taiwan stock watchlist threshold alerts."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Final
from zoneinfo import ZoneInfo

from app.config import (
    AppConfig,
    EnvSettings,
    StockWatchlistSymbolConfig,
    StockWatchlistTaskConfig,
    StockWatchlistThemeConfig,
    load_config,
    load_env_settings,
    validate_telegram_notifier_secrets,
)
from app.core import (
    SignalItem,
    finish_signal_run,
    get_signal_status,
    mark_signals_digest_reported,
    tw_market_skip_label,
    tw_market_skip_reason,
    upsert_signal,
)
from app.sources import BaseSource, build_tw_stock_mover_sources_for_names

from .base import BaseTask
from .market_task_support import (
    NotificationOutcome,
    begin_source_run,
    fail_source_run,
    resolve_task_db_path,
    send_optional_telegram_digest,
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

_ALERT_SOURCE: Final[str] = "stock_watchlist"
_ALERT_ITEM_TYPE: Final[str] = "stock_watchlist_alert"
_TAIPEI: Final[ZoneInfo] = ZoneInfo("Asia/Taipei")


@dataclass(slots=True)
class WatchlistRunSummary:
    """Collected counts for one watchlist task run."""

    successful_sources: int = 0
    fetched_quote_count: int = 0
    watched_quote_count: int = 0
    triggered_count: int = 0
    new_alert_count: int = 0
    duplicate_alert_count: int = 0
    triggered_alerts: list[SignalItem] = field(default_factory=list)
    new_alerts: list[SignalItem] = field(default_factory=list)
    pending_alerts: list[SignalItem] = field(default_factory=list)
    pending_signal_ids: list[int] = field(default_factory=list)
    failed_sources: list[tuple[str, str]] = field(default_factory=list)


class StockWatchlistTask(BaseTask):
    """Fetch quotes and alert when watchlist symbols breach thresholds."""

    name: ClassVar[str] = "stock_watchlist"

    def run(self) -> None:
        db_path = resolve_task_db_path()
        summary = WatchlistRunSummary()
        notification = NotificationOutcome(status="not_applicable")

        self.begin_task_run(db_path=db_path)
        self.emit_log(
            "task_started",
            message="Stock watchlist task started",
            db_path=str(db_path),
        )

        try:
            config = load_config(self.config_path)
            watchlist = _require_enabled_watchlist(config)
            env = load_env_settings()
            if self._skip_outside_market_hours(db_path=db_path):
                return

            symbol_by_code = {
                symbol.code: symbol for symbol in watchlist.symbols
            }
            quotes_by_code = self._collect_watched_quotes(
                config=config,
                watchlist=watchlist,
                symbol_by_code=symbol_by_code,
                summary=summary,
                db_path=db_path,
            )
            self._persist_triggered_alerts(
                quotes_by_code=quotes_by_code,
                symbol_by_code=symbol_by_code,
                watchlist=watchlist,
                summary=summary,
                db_path=db_path,
            )
            print(
                "\n".join(
                    _build_console_summary(
                        config_path=self.config_path,
                        db_path=db_path,
                        summary=summary,
                    )
                )
            )
            notification = self._maybe_send_telegram_digest(
                config=config,
                env=env,
                watchlist=watchlist,
                summary=summary,
                db_path=db_path,
            )
            self._finalize_success(
                summary=summary,
                notification=notification,
                db_path=db_path,
            )
        except Exception as exc:
            self.end_task_run(
                db_path=db_path,
                status="failed",
                fetched_count=summary.fetched_quote_count,
                new_count=summary.new_alert_count,
                skipped_count=summary.duplicate_alert_count,
                notified_count=notification.notified_count,
                error_message=str(exc),
            )
            self.emit_log(
                "task_failed",
                message="Stock watchlist task failed",
                level=logging.ERROR,
                exc_info=exc,
                db_path=str(db_path),
                error=str(exc),
            )
            raise

    def _skip_outside_market_hours(self, *, db_path: Path) -> bool:
        skip_reason = tw_market_skip_reason()
        if skip_reason is None:
            return False
        skip_label = tw_market_skip_label(skip_reason)
        self.emit_log(
            "task_skipped",
            message="Stock watchlist task skipped",
            db_path=str(db_path),
            skip_reason=skip_reason,
        )
        print(
            "stock_watchlist task skipped using "
            f"{self.config_path}: {skip_label}"
        )
        self.end_task_run(
            db_path=db_path,
            status="skipped",
            notified_count=0,
            error_message=skip_reason,
        )
        return True

    def _collect_watched_quotes(
        self,
        *,
        config: AppConfig,
        watchlist: StockWatchlistTaskConfig,
        symbol_by_code: dict[str, StockWatchlistSymbolConfig],
        summary: WatchlistRunSummary,
        db_path: Path,
    ) -> dict[str, SignalItem]:
        sources = build_tw_stock_mover_sources_for_names(
            config,
            watchlist.sources,
        )
        if not sources:
            raise ValueError("No Taiwan stock sources are configured.")

        quotes_by_code: dict[str, SignalItem] = {}
        for source in sources:
            self._fetch_one_source(
                source=source,
                symbol_by_code=symbol_by_code,
                quotes_by_code=quotes_by_code,
                summary=summary,
                db_path=db_path,
            )
        return quotes_by_code

    def _fetch_one_source(
        self,
        *,
        source: BaseSource,
        symbol_by_code: dict[str, StockWatchlistSymbolConfig],
        quotes_by_code: dict[str, SignalItem],
        summary: WatchlistRunSummary,
        db_path: Path,
    ) -> None:
        run_id = begin_source_run(
            self,
            source_name=source.name,
            db_path=db_path,
        )
        try:
            fetched_items = source.fetch()
            stock_items = [
                item
                for item in fetched_items
                if item.item_type == "tw_stock_mover"
            ]
            matched = _merge_watched_quotes(
                stock_items,
                symbol_by_code=symbol_by_code,
                quotes_by_code=quotes_by_code,
            )
            summary.fetched_quote_count += len(stock_items)
            summary.watched_quote_count += matched
            finish_signal_run(
                run_id,
                status="completed",
                fetched_count=len(stock_items),
                inserted_count=0,
                updated_count=0,
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
                watched_count=matched,
            )
            summary.successful_sources += 1
        except Exception as exc:
            fail_source_run(
                self,
                source_name=source.name,
                run_id=run_id,
                error=exc,
                db_path=db_path,
                failed_sources=summary.failed_sources,
            )

    def _persist_triggered_alerts(
        self,
        *,
        quotes_by_code: dict[str, SignalItem],
        symbol_by_code: dict[str, StockWatchlistSymbolConfig],
        watchlist: StockWatchlistTaskConfig,
        summary: WatchlistRunSummary,
        db_path: Path,
    ) -> None:
        alert_items = _build_triggered_alerts(
            quotes_by_code=quotes_by_code,
            symbol_by_code=symbol_by_code,
            themes=watchlist.themes,
        )
        summary.triggered_count = len(alert_items)
        summary.triggered_alerts = alert_items
        for alert in alert_items:
            result = upsert_signal(alert, db_path=db_path)
            if result.action == "inserted":
                summary.new_alert_count += 1
                summary.new_alerts.append(alert)
                summary.pending_alerts.append(alert)
                summary.pending_signal_ids.append(result.signal_id)
                continue

            summary.duplicate_alert_count += 1
            # Keep unsent alerts pending so a Telegram failure can be retried.
            if get_signal_status(result.signal_id, db_path=db_path) == "new":
                summary.pending_alerts.append(alert)
                summary.pending_signal_ids.append(result.signal_id)

    def _finalize_success(
        self,
        *,
        summary: WatchlistRunSummary,
        notification: NotificationOutcome,
        db_path: Path,
    ) -> None:
        if summary.successful_sources == 0:
            failure_summary = "; ".join(
                f"{source_name}: {message}"
                for source_name, message in summary.failed_sources
            )
            self.emit_log(
                "task_summary",
                message="Stock watchlist task summary",
                db_path=str(db_path),
                task_status="failed",
                fetched_count=summary.fetched_quote_count,
                new_count=summary.new_alert_count,
                duplicate_count=summary.duplicate_alert_count,
                notified_count=notification.notified_count,
                notification_status=notification.status,
                successful_source_count=summary.successful_sources,
                failed_source_count=len(summary.failed_sources),
                triggered_count=summary.triggered_count,
            )
            raise ValueError(
                "All configured stock watchlist sources failed: "
                f"{failure_summary}"
            )

        self.emit_log(
            "task_completed",
            message="Stock watchlist task completed",
            db_path=str(db_path),
            fetched_count=summary.fetched_quote_count,
            new_count=summary.new_alert_count,
            duplicate_count=summary.duplicate_alert_count,
            notified_count=notification.notified_count,
            notification_status=notification.status,
            successful_source_count=summary.successful_sources,
            failed_source_count=len(summary.failed_sources),
            triggered_count=summary.triggered_count,
            watched_count=summary.watched_quote_count,
        )
        self.end_task_run(
            db_path=db_path,
            status="success",
            fetched_count=summary.fetched_quote_count,
            new_count=summary.new_alert_count,
            skipped_count=summary.duplicate_alert_count,
            notified_count=notification.notified_count,
        )

    def _maybe_send_telegram_digest(
        self,
        *,
        config: AppConfig,
        env: EnvSettings,
        watchlist: StockWatchlistTaskConfig,
        summary: WatchlistRunSummary,
        db_path: Path,
    ) -> NotificationOutcome:
        # Digests include pending (unreported) alerts so Telegram failures retry.
        digest = _build_telegram_digest(
            alerts=summary.pending_alerts,
            themes=watchlist.themes,
            timezone_name=watchlist.timezone,
            failed_sources=summary.failed_sources,
        )
        notification = send_optional_telegram_digest(
            self,
            digest=digest,
            config=config,
            env=env,
            db_path=db_path,
            notified_count=len(summary.pending_alerts),
            empty_status="skipped_no_alerts",
            empty_message=(
                "telegram digest skipped: no pending watchlist alerts"
            ),
            validate_secrets=validate_telegram_notifier_secrets,
            empty_log_fields={
                "triggered_count": summary.triggered_count,
                "duplicate_count": summary.duplicate_alert_count,
                "pending_count": len(summary.pending_alerts),
                "failed_source_count": len(summary.failed_sources),
            },
        )
        if notification.status == "sent" and summary.pending_signal_ids:
            mark_signals_digest_reported(
                summary.pending_signal_ids,
                db_path=db_path,
            )
        return notification


def _require_enabled_watchlist(config: AppConfig) -> StockWatchlistTaskConfig:
    watchlist = config.tasks.stock_watchlist
    if not watchlist.enabled:
        raise ValueError("Task 'stock_watchlist' is disabled in config")
    if not watchlist.symbols:
        raise ValueError("Task 'stock_watchlist' has no symbols configured")
    return watchlist


def _merge_watched_quotes(
    stock_items: list[SignalItem],
    *,
    symbol_by_code: dict[str, StockWatchlistSymbolConfig],
    quotes_by_code: dict[str, SignalItem],
) -> int:
    matched = 0
    for item in stock_items:
        code = item_stock_code(item)
        if code not in symbol_by_code:
            continue
        matched += 1
        existing = quotes_by_code.get(code)
        if existing is None or item.observed_at > existing.observed_at:
            quotes_by_code[code] = item
    return matched


def _build_triggered_alerts(
    *,
    quotes_by_code: dict[str, SignalItem],
    symbol_by_code: dict[str, StockWatchlistSymbolConfig],
    themes: dict[str, StockWatchlistThemeConfig],
) -> list[SignalItem]:
    alerts: list[SignalItem] = []
    for code, symbol in symbol_by_code.items():
        quote = quotes_by_code.get(code)
        if quote is None:
            continue
        pct = item_change_percent(quote)
        if abs(pct) < symbol.alert_change_percent_abs:
            continue
        alerts.append(
            _build_alert_item(
                quote=quote,
                symbol=symbol,
                change_percent=pct,
                themes=_theme_names_for_code(code, themes),
            )
        )
    alerts.sort(key=lambda item: abs(item_change_percent(item)), reverse=True)
    return alerts


def _build_alert_item(
    *,
    quote: SignalItem,
    symbol: StockWatchlistSymbolConfig,
    change_percent: float,
    themes: list[str],
) -> SignalItem:
    display_name = symbol.name.strip() or item_company_name(quote) or symbol.code
    trade_date, snapshot_key = _snapshot_parts(quote.observed_at)
    threshold = symbol.alert_change_percent_abs
    metadata = {
        "stock_code": symbol.code,
        "company": display_name,
        "change_percent": round(change_percent, 4),
        "alert_change_percent_abs": threshold,
        "market": quote.metadata.get("market"),
        "exchange": quote.metadata.get("exchange"),
        "price": quote.metadata.get("price") or quote.metadata.get("last_price"),
        "themes": themes,
        "quote_source": quote.source,
    }
    compact_metadata = {
        key: value
        for key, value in metadata.items()
        if value is not None and value != "" and value != []
    }
    return SignalItem(
        source=_ALERT_SOURCE,
        item_type=_ALERT_ITEM_TYPE,
        title=f"{symbol.code} {display_name} {change_percent:+.2f}%",
        observed_at=quote.observed_at,
        external_id=f"watchlist-{trade_date}-{snapshot_key}-{symbol.code}",
        content=(
            f"{symbol.code} {display_name} moved {change_percent:+.2f}% "
            f"(threshold {threshold:.1f}%)."
        ),
        metadata=compact_metadata,
    )


def _build_console_summary(
    *,
    config_path: str,
    db_path: Path,
    summary: WatchlistRunSummary,
) -> list[str]:
    lines = [
        f"stock_watchlist task executed using {config_path}:",
        (
            f"fetched {summary.fetched_quote_count} quote(s) from "
            f"{summary.successful_sources} successful source(s)"
        ),
        f"matched {summary.watched_quote_count} watched quote(s)",
        (
            f"triggered {summary.triggered_count} alert(s): "
            f"{summary.new_alert_count} new / "
            f"{summary.duplicate_alert_count} duplicate to {db_path}"
        ),
    ]
    for source_name, message in summary.failed_sources:
        lines.append(f"source failed: {source_name}: {message}")
    for alert in summary.pending_alerts:
        lines.append(f"- {_render_digest_alert_line(alert)}")
    return lines


def _build_telegram_digest(
    *,
    alerts: list[SignalItem],
    themes: dict[str, StockWatchlistThemeConfig],
    timezone_name: str,
    failed_sources: list[tuple[str, str]] | None = None,
) -> str:
    failed_sources = failed_sources or []
    if not alerts and not failed_sources:
        return ""

    lines = [_build_telegram_heading(timezone_name)]
    if alerts:
        lines.append("")
        lines.append("價格異動")
        for index, alert in enumerate(alerts, start=1):
            lines.append(f"{index}. {_render_digest_alert_line(alert)}")

        theme_lines = _render_theme_section(alerts=alerts, themes=themes)
        if theme_lines:
            lines.append("")
            lines.append("主題觀察")
            lines.extend(theme_lines)

    if failed_sources:
        lines.append("")
        lines.append("資料來源異常")
        for source_name, message in failed_sources:
            lines.append(f"- {source_warning_label(source_name)}：{message}")
    return "\n".join(lines)


def _build_telegram_heading(timezone_name: str) -> str:
    timestamp = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")
    return f"自選股異動快報 {timestamp}"


def _render_theme_section(
    *,
    alerts: list[SignalItem],
    themes: dict[str, StockWatchlistThemeConfig],
) -> list[str]:
    if not themes:
        return []
    triggered_codes = {item_stock_code(alert) for alert in alerts}
    lines: list[str] = []
    for theme_name, theme_config in themes.items():
        hit_count = sum(
            1 for code in theme_config.symbols if code in triggered_codes
        )
        if hit_count > 0:
            lines.append(f"{theme_name}: {hit_count} 檔觸發異動")
    return lines


def _render_digest_alert_line(alert: SignalItem) -> str:
    code = item_stock_code(alert)
    company = item_company_name(alert)
    pct = item_change_percent(alert)
    threshold = alert.metadata.get("alert_change_percent_abs")
    prefix = " ".join(part for part in (code, company) if part).strip()
    if not prefix:
        prefix = alert.title
    if isinstance(threshold, int | float):
        return (
            f"{prefix} {pct:+.2f}% — "
            f"超過設定門檻 {float(threshold):.1f}%"
        )
    return f"{prefix} {pct:+.2f}%"


def _theme_names_for_code(
    code: str,
    themes: dict[str, StockWatchlistThemeConfig],
) -> list[str]:
    return [
        theme_name
        for theme_name, theme_config in themes.items()
        if code in theme_config.symbols
    ]


def _snapshot_parts(observed_at: datetime) -> tuple[str, str]:
    localized = observed_at
    if localized.tzinfo is None:
        localized = localized.replace(tzinfo=_TAIPEI)
    localized = localized.astimezone(_TAIPEI)
    return localized.strftime("%Y%m%d"), localized.strftime("%H%M")


