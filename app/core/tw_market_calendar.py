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
TW_MARKET_SKIP_CALENDAR_UNAVAILABLE = "taiwan_market_calendar_unavailable"


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


def tw_market_calendar_years(
    holiday_dates: frozenset[date] | None = None,
) -> frozenset[int]:
    """Return calendar years covered by the holiday dataset."""

    dates = (
        holiday_dates
        if holiday_dates is not None
        else _cached_packaged_holiday_dates()
    )
    return frozenset(day.year for day in dates)


def is_tw_market_calendar_year_covered(
    year: int,
    *,
    holiday_dates: frozenset[date] | None = None,
) -> bool:
    """Return whether ``year`` has at least one entry in the holiday calendar."""

    return year in tw_market_calendar_years(holiday_dates)


def latest_tw_market_calendar_year(
    holiday_dates: frozenset[date] | None = None,
) -> int | None:
    """Return the newest year present in the holiday dataset, if any."""

    years = tw_market_calendar_years(holiday_dates)
    return max(years) if years else None


def tw_market_calendar_coverage_warning(
    *,
    today: date | None = None,
    warn_within_days: int = 60,
    holiday_dates: frozenset[date] | None = None,
) -> str | None:
    """Return an operator warning when calendar coverage is about to lapse (#103).

    Warns when the current year is missing, or when ``today`` is within
    ``warn_within_days`` of the end of the latest covered year.
    """

    if warn_within_days < 0:
        raise ValueError("warn_within_days must be zero or greater")
    current = today or date.today()
    years = tw_market_calendar_years(holiday_dates)
    if not years:
        return (
            "Taiwan market holiday calendar is empty; TW stock tasks fail closed"
        )
    if current.year not in years:
        return (
            f"Taiwan market holiday calendar has no entries for {current.year}; "
            "TW stock tasks will skip until app/data/tw_market_holidays.yaml "
            "is updated from TWSE"
        )
    latest = max(years)
    year_end = date(latest, 12, 31)
    days_left = (year_end - current).days
    if days_left <= warn_within_days:
        return (
            f"Taiwan market holiday calendar only covers through {latest}; "
            f"{days_left} day(s) remain before fail-closed skips begin. "
            "Update app/data/tw_market_holidays.yaml when TWSE publishes "
            f"{latest + 1}"
        )
    return None


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
