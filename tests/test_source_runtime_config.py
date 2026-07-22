from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest

from app.core import AppConfig
from app.sources import (
    GlassdoorSource,
    IndeedSource,
    Job104Source,
    TpexSource,
    TwseSource,
)


def assert_close(actual: float, expected: float) -> None:
    assert math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-9)


def _base_config_dict(*, sources: dict[str, object] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "agent": {"timezone": "Asia/Taipei", "digest_time": "20:00"},
        "tasks": {
            "job_search": {
                "enabled": True,
                "sources": ["job104"],
                "keywords": ["python"],
                "locations": ["Taiwan"],
            },
            "tw_stock_movers": {
                "enabled": True,
                "top_n": 20,
                "timezone": "Asia/Taipei",
            },
        },
        "notifiers": {"telegram": {"enabled": True}},
        "scoring": {
            "freshness_weight": 0.6,
            "priority_weight": 0.4,
        },
    }
    if sources is not None:
        payload["sources"] = sources
    return payload


@pytest.mark.parametrize(
    ("factory", "source_key", "settings", "expected"),
    [
        (
            Job104Source.from_config,
            "job104",
            {
                "timeout_seconds": 15,
                "max_retries": 4,
                "request_delay_seconds": 2.5,
                "page_limit": 3,
            },
            {
                "timeout_seconds": 15.0,
                "max_retries": 4,
                "request_delay_seconds": 2.5,
                "page_limit": 3,
            },
        ),
        (
            IndeedSource.from_config,
            "indeed",
            {
                "timeout_seconds": 12,
                "max_retries": 5,
                "request_delay_seconds": 0.5,
                "page_limit": 2,
            },
            {
                "timeout_seconds": 12.0,
                "max_retries": 5,
                "request_delay_seconds": 0.5,
                "page_limit": 2,
            },
        ),
        (
            GlassdoorSource.from_config,
            "glassdoor",
            {
                "timeout_seconds": 11,
                "max_retries": 3,
                "request_delay_seconds": 1.5,
                "page_limit": 4,
            },
            {
                "timeout_seconds": 11.0,
                "max_retries": 3,
                "request_delay_seconds": 1.5,
                "page_limit": 4,
            },
        ),
        (
            TwseSource.from_config,
            "twse",
            {
                "timeout_seconds": 9,
                "max_retries": 3,
                "request_delay_seconds": 0.4,
                "batch_size": 40,
            },
            {
                "timeout_seconds": 9.0,
                "max_retries": 3,
                "request_delay_seconds": 0.4,
                "batch_size": 40,
            },
        ),
        (
            TpexSource.from_config,
            "tpex",
            {
                "timeout_seconds": 7,
                "max_retries": 0,
                "request_delay_seconds": 0.3,
                "batch_size": 30,
            },
            {
                "timeout_seconds": 7.0,
                "max_retries": 0,
                "request_delay_seconds": 0.3,
                "batch_size": 30,
            },
        ),
    ],
)
def test_source_from_config_applies_sources_runtime_settings(
    factory: Callable[[AppConfig], Any],
    source_key: str,
    settings: dict[str, object],
    expected: dict[str, float | int],
) -> None:
    source = factory(
        AppConfig.from_dict(_base_config_dict(sources={source_key: settings}))
    )

    assert_close(source.timeout_seconds, float(expected["timeout_seconds"]))
    assert source.max_retries == expected["max_retries"]
    assert_close(
        source.request_delay_seconds,
        float(expected["request_delay_seconds"]),
    )
    if "page_limit" in expected:
        assert source.page_limit == expected["page_limit"]
    if "batch_size" in expected:
        assert source.batch_size == expected["batch_size"]
