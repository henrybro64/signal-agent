"""Sample fetch task for exercising the source pipeline."""

from __future__ import annotations

from typing import ClassVar

from app.config import load_config, load_env_settings, validate_llm_scoring_secrets
from app.sources import DummySource, fetch_from_sources

from .base import BaseTask


class FetchTask(BaseTask):
    """Fetch sample signals from the dummy source."""

    name: ClassVar[str] = "fetch"

    def run(self) -> None:
        config = load_config(self.config_path)
        env = load_env_settings()
        validate_llm_scoring_secrets(
            llm_enabled=config.scoring.llm_enabled,
            env=env,
        )
        items = fetch_from_sources([DummySource()])

        lines = [
            f"fetch task executed using {self.config_path}:",
            f"fetched {len(items)} signal(s) from dummy source:",
        ]
        lines.extend(f"- [{item.item_type}] {item.title}" for item in items)
        print("\n".join(lines))
