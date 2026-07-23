"""Time-related helpers for scheduler and market-hour checks."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from .tw_market_calendar import (
    TW_MARKET_SKIP_CALENDAR_UNAVAILABLE,
    TW_MARKET_SKIP_CLOSED,
    TW_MARKET_SKIP_HOLIDAY,
    is_tw_market_calendar_year_covered,
    is_tw_market_holiday,
)

TW_TIMEZONE = ZoneInfo("Asia/Taipei")
_MARKET_OPEN_TIME = time(9, 0)
_MARKET_CLOSE_TIME = time(13, 30)


def localize_to_tw(now: datetime | None = None) -> datetime:
    """Return ``now`` converted to ``Asia/Taipei``.

    When ``now`` is omitted, uses the current UTC instant. Naive datetimes are
    treated as Taipei local time.
    """

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TW_TIMEZONE)
    return current.astimezone(TW_TIMEZONE)


def tw_market_skip_reason(now: datetime | None = None) -> str | None:
    """Return a skip reason when the Taiwan market is not open.

    Returns ``None`` when the market is open. Otherwise returns:

    - ``taiwan_market_calendar_unavailable`` when the static holiday file has
      no entries for the requested year (fail closed until the YAML is updated)
    - ``taiwan_market_holiday`` for configured no-trade dates
    - ``taiwan_market_closed`` for weekends and outside 09:00–13:30
    """

    localized = localize_to_tw(now)
    if not is_tw_market_calendar_year_covered(localized.year):
        return TW_MARKET_SKIP_CALENDAR_UNAVAILABLE
    if is_tw_market_holiday(localized.date()):
        return TW_MARKET_SKIP_HOLIDAY
    if localized.weekday() >= 5:
        return TW_MARKET_SKIP_CLOSED

    current_time = localized.time()
    if current_time < _MARKET_OPEN_TIME or current_time > _MARKET_CLOSE_TIME:
        return TW_MARKET_SKIP_CLOSED
    return None


def tw_market_skip_label(skip_reason: str) -> str:
    """Return a human-readable label for a Taiwan market skip reason."""

    if skip_reason == TW_MARKET_SKIP_HOLIDAY:
        return "Taiwan market holiday"
    if skip_reason == TW_MARKET_SKIP_CALENDAR_UNAVAILABLE:
        return "Taiwan market calendar unavailable"
    return "Taiwan market is closed"


def is_tw_market_open(now: datetime | None = None) -> bool:
    """Return whether the Taiwan market is open at the given time.

    Market hours are Monday through Friday, 09:00 through 13:30 inclusive, in
    the ``Asia/Taipei`` timezone, excluding configured Taiwan market holidays
    and settlement-only no-trade days. Years with no packaged holiday entries
    fail closed.

    When ``now`` is timezone-aware, it is converted to Taipei before the check.
    Naive datetimes are treated as Taipei local time.
    """

    return tw_market_skip_reason(now) is None
