"""Dummy task used to validate CLI and config wiring."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import ClassVar

from app.config import (
    has_telegram_credentials,
    load_config,
    load_env_settings,
    validate_llm_scoring_secrets,
)
from app.core.secrets import resolve_provider_credentials

from .base import BaseTask
from .fetch import DB_PATH_ENV_VAR, DEFAULT_DB_PATH


class DummyTask(BaseTask):
    """Print a bootstrap summary for local development checks."""

    name: ClassVar[str] = "dummy"

    def run(self) -> None:
        db_path = _resolve_db_path()
        self.begin_task_run(db_path=db_path)
        self.emit_log(
            "task_started",
            message="Dummy task started",
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
            )
            telegram_ready = has_telegram_credentials(env)

            print(
                f"dummy task executed using {self.config_path}: "
                f"timezone={config.agent.timezone}, "
                f"digest_time={config.agent.digest_time}, "
                f"job_search_enabled={config.tasks.job_search.enabled}, "
                f"tw_stock_movers_enabled={config.tasks.tw_stock_movers.enabled}, "
                f"stock_watchlist_enabled="
                f"{config.tasks.stock_watchlist.enabled}, "
                f"telegram_ready={telegram_ready}"
            )

            self.end_task_run(
                db_path=db_path,
                status="success",
                notified_count=0,
            )
            self.emit_log(
                "task_completed",
                message="Dummy task completed",
                db_path=str(db_path),
                notification_status="not_applicable",
                notified_count=0,
                telegram_ready=telegram_ready,
            )
        except Exception as exc:
            self.end_task_run(
                db_path=db_path,
                status="failed",
                error_message=str(exc),
            )
            self.emit_log(
                "task_failed",
                message="Dummy task failed",
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
