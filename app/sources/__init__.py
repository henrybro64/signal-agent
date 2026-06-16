"""Signal source collectors for jobs, markets, and other personal inputs."""

from .base import BaseSource, fetch_from_sources
from .dummy import DummySource

__all__ = ["BaseSource", "DummySource", "fetch_from_sources"]
