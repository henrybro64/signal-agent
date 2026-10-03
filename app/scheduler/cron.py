"""Lightweight cron-like next-run helpers (#85).

Supports:
- ``every:<minutes>`` interval schedules
- 5-field cron with ``*``, ``*/N``, and integer fields
- one-shot ``run_at`` ISO timestamps (handled by store)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


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
    cursor = (local_after + timedelta(minutes=1)).replace(
        second=0,
        microsecond=0,
    )
    # Bound search to ~2 years to avoid infinite loops on impossible cron.
    limit = cursor + timedelta(days=800)
    while cursor <= limit:
        if (
            _match(cursor.minute, minute_f)
            and _match(cursor.hour, hour_f)
            and _match(cursor.day, dom_f)
            and _match(cursor.month, month_f)
            and _match((cursor.weekday() + 1) % 7, dow_f)  # cron: 0=Sun
        ):
            return cursor
        cursor += timedelta(minutes=1)
    return None


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
