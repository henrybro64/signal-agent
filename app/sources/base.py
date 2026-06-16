"""Base interfaces and helpers for signal sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import ClassVar

from app.core import SignalItem


class BaseSource(ABC):
    """Common interface for any signal source plugin."""

    name: ClassVar[str]

    @abstractmethod
    def fetch(self) -> list[SignalItem]:
        """Fetch and return normalized signal items."""


SourceErrorHandler = Callable[[BaseSource, Exception], None]


def fetch_from_sources(
    sources: Iterable[BaseSource],
    *,
    on_error: SourceErrorHandler | None = None,
) -> list[SignalItem]:
    """Run a simple source pipeline and flatten all fetched items."""

    items: list[SignalItem] = []
    for source in sources:
        try:
            items.extend(source.fetch())
        except Exception as exc:
            if on_error is None:
                raise
            on_error(source, exc)
    return items
