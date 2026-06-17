"""Sample fetch task for exercising the source pipeline."""

from __future__ import annotations

from app.config import load_config
from app.sources import DummySource, fetch_from_sources

from .base import BaseTask


class FetchTask(BaseTask):
    """Fetch sample signals from the dummy source."""

    name = "fetch"

    def run(self) -> None:
        load_config(self.config_path)
        items = fetch_from_sources([DummySource()])

        lines = [
            f"fetch task executed using {self.config_path}:",
            f"fetched {len(items)} signal(s) from dummy source:",
        ]
        lines.extend(f"- [{item.item_type}] {item.title}" for item in items)
        print("\n".join(lines))
