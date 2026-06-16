"""Core data models shared across signal sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SignalItem:
    """Normalized signal from any source.

    ``content`` holds human-readable body text (job description, news snippet).
    ``metadata`` holds structured, source-specific fields (company, stock_code, etc.).
    """

    source: str
    item_type: str
    title: str
    observed_at: datetime
    url: str | None = None
    external_id: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
