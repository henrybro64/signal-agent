"""Static Taiwan stock-market holiday calendar."""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_HOLIDAY_RESOURCE = "tw_market_holidays.yaml"
_DEFAULT_PACKAGE = "app.data"

TW_MARKET_SKIP_HOLIDAY = "taiwan_market_holiday"
TW_MARKET_SKIP_CLOSED = "taiwan_market_closed"


class TwMarketCalendarError(ValueError):
    """Raised when the packaged holiday calendar cannot be loaded."""


def load_tw_market_holiday_dates(path: Path | str | None = None) -> frozenset[date]:
    """Load no-trade dates from a YAML holiday file.

    When ``path`` is omitted, the packaged ``app/data/tw_market_holidays.yaml``
    resource is used.
    """

    if path is None:
        resource = files(_DEFAULT_PACKAGE).joinpath(_DEFAULT_HOLIDAY_RESOURCE)
        raw_text = resource.read_text(encoding="utf-8")
        source_label = f"package:{_DEFAULT_PACKAGE}/{_DEFAULT_HOLIDAY_RESOURCE}"
    else:
        holiday_path = Path(path)
        try:
            raw_text = holiday_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TwMarketCalendarError(
                f"Unable to read Taiwan market holiday file: {holiday_path}"
            ) from exc
        source_label = str(holiday_path)

    try:
        payload = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise TwMarketCalendarError(
            f"Invalid YAML in Taiwan market holiday file: {source_label}"
        ) from exc

    return _parse_holiday_payload(payload, source_label=source_label)


def is_tw_market_holiday(
    day: date | datetime,
    *,
    holiday_dates: frozenset[date] | None = None,
) -> bool:
    """Return whether ``day`` is a configured Taiwan market no-trade date."""

    target = day.date() if isinstance(day, datetime) else day
    dates = (
        holiday_dates
        if holiday_dates is not None
        else _cached_packaged_holiday_dates()
    )
    return target in dates


def clear_tw_market_holiday_cache() -> None:
    """Clear the packaged holiday cache (primarily for tests)."""

    _cached_packaged_holiday_dates.cache_clear()


@lru_cache(maxsize=1)
def _cached_packaged_holiday_dates() -> frozenset[date]:
    return load_tw_market_holiday_dates()


def _parse_holiday_payload(
    payload: Any,
    *,
    source_label: str,
) -> frozenset[date]:
    if not isinstance(payload, dict):
        raise TwMarketCalendarError(
            f"Taiwan market holiday file must be a mapping: {source_label}"
        )

    holidays = payload.get("holidays")
    if holidays is None:
        raise TwMarketCalendarError(
            f"Taiwan market holiday file missing 'holidays': {source_label}"
        )
    if not isinstance(holidays, list):
        raise TwMarketCalendarError(
            f"'holidays' must be a list in {source_label}"
        )

    parsed: set[date] = set()
    for index, entry in enumerate(holidays):
        if not isinstance(entry, dict):
            raise TwMarketCalendarError(
                f"Holiday entry {index} must be a mapping in {source_label}"
            )
        raw_date = entry.get("date")
        if raw_date is None:
            raise TwMarketCalendarError(
                f"Holiday entry {index} missing 'date' in {source_label}"
            )
        if isinstance(raw_date, datetime):
            parsed.add(raw_date.date())
            continue
        if isinstance(raw_date, date):
            parsed.add(raw_date)
            continue
        if isinstance(raw_date, str):
            try:
                parsed.add(date.fromisoformat(raw_date.strip()))
            except ValueError as exc:
                raise TwMarketCalendarError(
                    f"Invalid holiday date {raw_date!r} in {source_label}"
                ) from exc
            continue
        raise TwMarketCalendarError(
            f"Unsupported holiday date type in {source_label}: {raw_date!r}"
        )

    return frozenset(parsed)
