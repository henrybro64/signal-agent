"""Dummy task used to validate CLI and config wiring."""

from __future__ import annotations

from typing import ClassVar

from app.config import (
    has_telegram_credentials,
    load_config,
    load_env_settings,
    validate_llm_scoring_secrets,
)

from .base import BaseTask


class DummyTask(BaseTask):
    """Print a bootstrap summary for local development checks."""

    name: ClassVar[str] = "dummy"

    def run(self) -> None:
        self.emit_log(
            "task_started",
            message="Dummy task started",
        )

        config = load_config(self.config_path)
        env = load_env_settings()
        validate_llm_scoring_secrets(
            llm_enabled=config.scoring.llm_enabled,
            env=env,
        )
        telegram_ready = has_telegram_credentials(env)

        print(
            f"dummy task executed using {self.config_path}: "
            f"timezone={config.agent.timezone}, "
            f"digest_time={config.agent.digest_time}, "
            f"job_search_enabled={config.tasks.job_search.enabled}, "
            f"tw_stock_movers_enabled={config.tasks.tw_stock_movers.enabled}, "
            f"telegram_ready={telegram_ready}"
        )

        self.emit_log(
            "task_completed",
            message="Dummy task completed",
            notification_status="not_applicable",
            notified_count=0,
            telegram_ready=telegram_ready,
        )
