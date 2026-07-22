"""Shared HTTP fetch runtime validation for configurable sources."""

from __future__ import annotations


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
