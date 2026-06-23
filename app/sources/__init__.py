"""Signal source collectors for jobs, markets, and other personal inputs."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from app.core.config import AppConfig

from .base import BaseSource, SourceErrorHandler, fetch_from_sources
from .dummy import DummySource
from .glassdoor import GlassdoorSource, GlassdoorSourceError
from .indeed import IndeedSource, IndeedSourceError
from .job104 import Job104Source, Job104SourceError
from .linkedin_email import LinkedInEmailSource, LinkedInEmailSourceError
from .tpex import TpexSource, TpexSourceError
from .twse import TwseSource, TwseSourceError

SourceFactory = Callable[[AppConfig], BaseSource]
UnknownSourceHandler = Callable[[str], None]


def _build_dummy_source(_config: AppConfig) -> BaseSource:
    return DummySource()


SOURCE_FACTORIES: dict[str, SourceFactory] = {
    DummySource.name: _build_dummy_source,
    GlassdoorSource.name: GlassdoorSource.from_config,
    IndeedSource.name: IndeedSource.from_config,
    Job104Source.name: Job104Source.from_config,
    LinkedInEmailSource.name: LinkedInEmailSource.from_config,
}

TW_STOCK_MOVER_SOURCE_FACTORIES: dict[str, SourceFactory] = {
    TwseSource.name: TwseSource.from_config,
}


def build_sources(
    config: AppConfig,
    names: Iterable[str],
    *,
    on_unknown: UnknownSourceHandler | None = None,
) -> list[BaseSource]:
    """Build configured source instances in order."""

    sources: list[BaseSource] = []
    supported_sources = ", ".join(list_source_names())

    for name in names:
        factory = SOURCE_FACTORIES.get(name)
        if factory is None:
            if on_unknown is None:
                raise ValueError(
                    f"Unsupported source: {name}. "
                    f"Supported sources: {supported_sources}"
                )
            on_unknown(name)
            continue
        sources.append(factory(config))

    return sources


def build_job_search_sources(
    config: AppConfig,
    *,
    on_unknown: UnknownSourceHandler | None = None,
) -> list[BaseSource]:
    """Build only the configured job-search sources."""

    return build_sources(
        config,
        config.tasks.job_search.sources,
        on_unknown=on_unknown,
    )


def build_tw_stock_mover_sources(config: AppConfig) -> list[BaseSource]:
    """Build the currently supported Taiwan stock-mover sources."""

    return [
        factory(config)
        for factory in TW_STOCK_MOVER_SOURCE_FACTORIES.values()
    ]


def list_source_names() -> list[str]:
    """Return the available source names."""

    return sorted(SOURCE_FACTORIES)


__all__ = [
    "BaseSource",
    "build_job_search_sources",
    "build_tw_stock_mover_sources",
    "build_sources",
    "DummySource",
    "GlassdoorSource",
    "GlassdoorSourceError",
    "IndeedSource",
    "IndeedSourceError",
    "Job104Source",
    "Job104SourceError",
    "LinkedInEmailSource",
    "LinkedInEmailSourceError",
    "list_source_names",
    "SOURCE_FACTORIES",
    "SourceErrorHandler",
    "SourceFactory",
    "TpexSource",
    "TpexSourceError",
    "TwseSource",
    "TwseSourceError",
    "UnknownSourceHandler",
    "fetch_from_sources",
]
