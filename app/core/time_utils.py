"""Time-related helpers for scheduler and market-hour checks."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

TW_TIMEZONE = ZoneInfo("Asia/Taipei")
_MARKET_OPEN_TIME = time(9, 0)
_MARKET_CLOSE_TIME = time(13, 30)


def is_tw_market_open(now: datetime | None = None) -> bool:
    """Return whether the Taiwan market is open at the given time.

    Market hours for the MVP are Monday through Friday, 09:00 through 13:30
    inclusive, in the ``Asia/Taipei`` timezone. Holiday handling is
    intentionally out of scope for this first version.

    When ``now`` is timezone-aware, it is converted to Taipei before the check.
    Naive datetimes are treated as Taipei local time.
    """

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TW_TIMEZONE)
    localized = current.astimezone(TW_TIMEZONE)

    if localized.weekday() >= 5:
        return False

    current_time = localized.time()
    return _MARKET_OPEN_TIME <= current_time <= _MARKET_CLOSE_TIME
