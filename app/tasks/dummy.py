"""Dummy task used to validate CLI and config wiring."""

from __future__ import annotations

from typing import ClassVar

from app.config import load_config, load_env_settings

from .base import BaseTask


class DummyTask(BaseTask):
    """Print a bootstrap summary for local development checks."""

    name: ClassVar[str] = "dummy"

    def run(self) -> None:
        config = load_config(self.config_path)
        env = load_env_settings()
        telegram_ready = bool(env.telegram_bot_token and env.telegram_chat_id)

        print(
            f"dummy task executed using {self.config_path}: "
            f"timezone={config.agent.timezone}, "
            f"digest_time={config.agent.digest_time}, "
            f"job_search_enabled={config.tasks.job_search.enabled}, "
            f"tw_stock_movers_enabled={config.tasks.tw_stock_movers.enabled}, "
            f"telegram_ready={telegram_ready}"
        )
