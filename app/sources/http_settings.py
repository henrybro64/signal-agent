"""Shared HTTP fetch runtime helpers for configurable sources."""

from __future__ import annotations

import time
from collections.abc import Callable

import requests

from app.core.config import (
    AppConfig,
    JobHttpSourceConfig,
    TwStockHttpSourceConfig,
    apply_optional_overrides,
)


def validate_http_fetch_settings(
    *,
    timeout_seconds: float,
    request_delay_seconds: float,
    max_retries: int,
) -> None:
    """Raise ValueError when shared HTTP runtime knobs are invalid."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if request_delay_seconds < 0:
        raise ValueError("request_delay_seconds must be zero or greater")
    if max_retries < 0:
        raise ValueError("max_retries must be zero or greater")


def build_job_http_source[TJobSource](
    source_cls: type[TJobSource],
    config: AppConfig,
    settings: JobHttpSourceConfig,
    *,
    session: requests.Session | None = None,
    request_delay_seconds: float | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    page_limit: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> TJobSource:
    """Construct a job HTTP source from config settings and optional overrides."""

    resolved = apply_optional_overrides(
        settings,
        request_delay_seconds=request_delay_seconds,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        page_limit=page_limit,
    )
    return source_cls(
        keywords=config.tasks.job_search.keywords,
        locations=config.tasks.job_search.locations,
        session=session,
        request_delay_seconds=resolved.request_delay_seconds,
        timeout_seconds=resolved.timeout_seconds,
        max_retries=resolved.max_retries,
        page_limit=resolved.page_limit,
        sleeper=sleeper,
    )


def build_tw_stock_http_source[TStockSource](
    source_cls: type[TStockSource],
    settings: TwStockHttpSourceConfig,
    *,
    session: requests.Session | None = None,
    request_delay_seconds: float | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    batch_size: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> TStockSource:
    """Construct a TW stock HTTP source from config settings and overrides."""

    resolved = apply_optional_overrides(
        settings,
        request_delay_seconds=request_delay_seconds,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        batch_size=batch_size,
    )
    return source_cls(
        session=session,
        request_delay_seconds=resolved.request_delay_seconds,
        timeout_seconds=resolved.timeout_seconds,
        max_retries=resolved.max_retries,
        batch_size=resolved.batch_size,
        sleeper=sleeper,
    )
