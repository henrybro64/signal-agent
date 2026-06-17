"""Signal source collectors for jobs, markets, and other personal inputs."""

from .base import BaseSource, SourceErrorHandler, fetch_from_sources
from .dummy import DummySource

__all__ = [
    "BaseSource",
    "DummySource",
    "SourceErrorHandler",
    "fetch_from_sources",
]
