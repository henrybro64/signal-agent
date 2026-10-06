"""Fetch configured job-search sources and persist them to SQLite."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import ClassVar

from app.config import load_config, load_env_settings, validate_llm_scoring_secrets
from app.core import finish_signal_run, start_signal_run, upsert_signal
from app.core.secrets import resolve_provider_credentials
from app.sources import build_job_search_sources

from .base import BaseTask

DEFAULT_DB_PATH = Path("data/signals.sqlite3")
DB_PATH_ENV_VAR = "SIGNAL_AGENT_DB_PATH"


class FetchTask(BaseTask):
    """Fetch configured signals, persist them, and report a run summary."""

    name: ClassVar[str] = "fetch"

    def run(self) -> None:
        db_path = _resolve_db_path()
        items = []
        completed_sources = 0
        inserted_count = 0
        updated_count = 0
        failed_sources: list[tuple[str, str]] = []
        unsupported_sources: list[str] = []

        self.begin_task_run(db_path=db_path)
        self.emit_log(
            "task_started",
            message="Fetch task started",
            db_path=str(db_path),
        )

        try:
            config = load_config(self.config_path)
            env = load_env_settings()
            env = resolve_provider_credentials(env, config.providers)
            validate_llm_scoring_secrets(
                llm_enabled=config.scoring.llm_enabled,
                env=env,
                provider=config.llm.provider,
                fallback_providers=[
                    item.provider for item in config.llm.fallbacks
                ],
            )

            sources = build_job_search_sources(
                config,
                on_unknown=unsupported_sources.append,
            )
            if not sources:
                if unsupported_sources:
                    unsupported = ", ".join(unsupported_sources)
                    raise ValueError(
                        "No supported job-search sources are configured. "
                        f"Unsupported sources: {unsupported}"
                    )
                raise ValueError("No job-search sources are configured.")

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
                    source_inserted = 0
                    source_updated = 0

                    for item in fetched_items:
                        result = upsert_signal(item, db_path=db_path)
                        if result.action == "inserted":
                            source_inserted += 1
                        else:
                            source_updated += 1

                    finish_signal_run(
                        run_id,
                        status="completed",
                        fetched_count=len(fetched_items),
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
                        new_count=source_inserted,
                        duplicate_count=source_updated,
                        skipped_count=source_updated,
                    )
                    items.extend(fetched_items)
                    inserted_count += source_inserted
                    updated_count += source_updated
                    completed_sources += 1
                except Exception as exc:
                    finish_signal_run(
                        run_id,
                        status="failed",
                        error=str(exc),
                        db_path=db_path,
                    )
                    failed_sources.append((source.name, str(exc)))
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

            if completed_sources == 0:
                failure_summary = "; ".join(
                    f"{source_name}: {message}"
                    for source_name, message in failed_sources
                )
                raise ValueError(
                    f"All configured job-search sources failed: {failure_summary}"
                )

            self.emit_log(
                "task_completed",
                message="Fetch task completed",
                db_path=str(db_path),
                fetched_count=len(items),
                new_count=inserted_count,
                duplicate_count=updated_count,
                skipped_count=updated_count,
                notified_count=0,
                notification_status="not_applicable",
                successful_source_count=completed_sources,
                failed_source_count=len(failed_sources),
                unsupported_source_count=len(unsupported_sources),
                unsupported_sources=unsupported_sources,
            )
            self.end_task_run(
                db_path=db_path,
                status="success",
                fetched_count=len(items),
                new_count=inserted_count,
                skipped_count=updated_count,
                notified_count=0,
            )

            lines = [
                f"fetch task executed using {self.config_path}:",
                (
                    f"fetched {len(items)} signal(s) from "
                    f"{completed_sources} successful source(s):"
                ),
                (
                    f"persisted {inserted_count} inserted / {updated_count} updated "
                    f"to {db_path}"
                ),
            ]
            if unsupported_sources:
                lines.append(
                    "skipped unsupported sources: " + ", ".join(unsupported_sources)
                )
            for source_name, message in failed_sources:
                lines.append(f"source failed: {source_name}: {message}")
            lines.extend(f"- [{item.item_type}] {item.title}" for item in items)
            print("\n".join(lines))
        except Exception as exc:
            self.end_task_run(
                db_path=db_path,
                status="failed",
                fetched_count=len(items),
                new_count=inserted_count,
                skipped_count=updated_count,
                notified_count=0,
                error_message=str(exc),
            )
            self.emit_log(
                "task_failed",
                message="Fetch task failed",
                level=logging.ERROR,
                exc_info=exc,
                db_path=str(db_path),
                error=str(exc),
            )
            raise


def _resolve_db_path() -> Path:
    raw_path = os.getenv(DB_PATH_ENV_VAR)
    if raw_path:
        return Path(raw_path).expanduser()
    return DEFAULT_DB_PATH
