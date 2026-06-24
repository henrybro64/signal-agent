"""Dummy source used for local development and tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from app.core import SignalItem

from .base import BaseSource

SAMPLE_OBSERVED_AT = datetime(2026, 6, 16, 9, 0, tzinfo=UTC)


class DummySource(BaseSource):
    """In-memory source that returns stable sample signals."""

    name: ClassVar[str] = "dummy"

    def fetch(self) -> list[SignalItem]:
        return [
            SignalItem(
                source=self.name,
                item_type="job",
                title="Python Backend Engineer",
                observed_at=SAMPLE_OBSERVED_AT,
                external_id="dummy-job-001",
                url="https://example.com/jobs/python-backend-engineer",
                content="Sample backend role for CLI and pipeline validation.",
                metadata={
                    "company": "Example Labs",
                    "location": "Taipei",
                    "employment_type": "full-time",
                },
            ),
            SignalItem(
                source=self.name,
                item_type="tw_stock_mover",
                title="TSMC 上漲 3.1%",
                observed_at=SAMPLE_OBSERVED_AT,
                external_id="dummy-stock-2330",
                content="Sample Taiwan stock mover used for development flows.",
                metadata={
                    "stock_code": "2330",
                    "company": "TSMC",
                    "change_percent": 3.1,
                    "direction": "up",
                    "market": "twse",
                    "exchange": "tse",
                },
            ),
        ]
