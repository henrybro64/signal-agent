"""Base interfaces and helpers for signal sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from app.core import SignalItem


class BaseSource(ABC):
    """Common interface for any signal source plugin."""

    name: str

    @abstractmethod
    def fetch(self) -> list[SignalItem]:
        """Fetch and return normalized signal items."""


def fetch_from_sources(sources: Iterable[BaseSource]) -> list[SignalItem]:
    """Run a simple source pipeline and flatten all fetched items."""

    items: list[SignalItem] = []
    for source in sources:
        items.extend(source.fetch())
    return items
