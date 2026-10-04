"""Lightweight cron-like next-run helpers (#85).

Supports:
- ``every:<minutes>`` interval schedules
- 5-field cron with ``*``, ``*/N``, and integer fields
- one-shot ``run_at`` ISO timestamps (handled by store)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# Cover a full Gregorian leap cycle so expressions like ``0 9 29 2 *`` still
# resolve when the next match is more than two years away.
_SEARCH_HORIZON_DAYS = 1600


def compute_next_run(
    *,
    cron: str | None,
    after: datetime,
    timezone_name: str,
) -> datetime | None:
    """Return the next run instant (timezone-aware) strictly after ``after``."""

    if not cron or not str(cron).strip():
        return None
    expression = str(cron).strip()
    tz = ZoneInfo(timezone_name)
    local_after = after.astimezone(tz)

    if expression.lower().startswith("every:"):
        minutes = int(expression.split(":", 1)[1].strip())
        if minutes <= 0:
            raise ValueError("every: minutes must be positive")
        return local_after + timedelta(minutes=minutes)

    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(
            "cron must be 5 fields (min hour dom month dow) or every:<minutes>"
        )
    minute_f, hour_f, dom_f, month_f, dow_f = fields

    # Advance in UTC minutes, then project back to local wall time. This avoids
    # constructing nonexistent local times across DST spring-forward gaps.
    cursor_utc = (
        (local_after + timedelta(minutes=1))
        .astimezone(UTC)
        .replace(
            second=0,
            microsecond=0,
        )
    )
    limit = cursor_utc + timedelta(days=_SEARCH_HORIZON_DAYS)
    while cursor_utc <= limit:
        local = cursor_utc.astimezone(tz)
        if (
            _match(local.minute, minute_f)
            and _match(local.hour, hour_f)
            and _match(local.day, dom_f)
            and _match(local.month, month_f)
            and _match((local.weekday() + 1) % 7, dow_f)  # cron: 0=Sun
        ):
            # Reject fold/gap artifacts: wall clock must round-trip via UTC.
            if _is_real_local_wall_time(local):
                return local
        cursor_utc += timedelta(minutes=1)
    return None


def _is_real_local_wall_time(value: datetime) -> bool:
    if value.tzinfo is None:
        return False
    round_trip = value.astimezone(UTC).astimezone(value.tzinfo)
    return (
        round_trip.year,
        round_trip.month,
        round_trip.day,
        round_trip.hour,
        round_trip.minute,
    ) == (value.year, value.month, value.day, value.hour, value.minute)


def _match(value: int, field: str) -> bool:
    field = field.strip()
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        if step <= 0:
            raise ValueError("cron step must be positive")
        return value % step == 0
    if "," in field:
        return any(_match(value, part) for part in field.split(","))
    if "-" in field:
        start_s, end_s = field.split("-", 1)
        return int(start_s) <= value <= int(end_s)
    return value == int(field)
