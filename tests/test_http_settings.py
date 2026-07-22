from __future__ import annotations

import pytest

from app.sources.http_settings import validate_http_fetch_settings


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"timeout_seconds": 0.0}, "timeout_seconds"),
        ({"request_delay_seconds": -0.1}, "request_delay_seconds"),
        ({"max_retries": -1}, "max_retries"),
    ],
)
def test_validate_http_fetch_settings_rejects_invalid_values(
    kwargs: dict[str, float | int],
    match: str,
) -> None:
    values: dict[str, float | int] = {
        "timeout_seconds": 10.0,
        "request_delay_seconds": 1.0,
        "max_retries": 2,
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=match):
        validate_http_fetch_settings(**values)  # type: ignore[arg-type]


def test_validate_http_fetch_settings_allows_zero_delay_and_retries() -> None:
    validate_http_fetch_settings(
        timeout_seconds=0.1,
        request_delay_seconds=0.0,
        max_retries=0,
    )
