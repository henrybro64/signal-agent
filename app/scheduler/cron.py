"""Lightweight cron-like next-run helpers (#85).

Supports:
- ``every:<minutes>`` interval schedules
- 5-field cron with ``*``, ``*/N``, lists, ranges, and ``start-end/step``
- one-shot ``run_at`` ISO timestamps (handled by store)

Day-of-week accepts ``0``–``7`` (``7`` is Sunday, normalized to ``0``).
When both day-of-month and day-of-week are restricted, matching uses
standard cron OR semantics (#101).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# Cover a full Gregorian leap cycle so expressions like ``0 9 29 2 *`` still
# resolve when the next match is more than two years away.
_SEARCH_HORIZON_DAYS = 1600

_FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "month": (1, 12),
    "dow": (0, 7),
}


@dataclass(frozen=True, slots=True)
class _CronFields:
    minutes: frozenset[int]
    hours: frozenset[int]
    dom: frozenset[int]
    months: frozenset[int]
    dow: frozenset[int]
    dom_unrestricted: bool
    dow_unrestricted: bool


def compute_next_run(
    *,
    cron: str | None,
    after: datetime,
    timezone_name: str,
) -> datetime | None:
    """Return the next run instant (timezone-aware) strictly after ``after``.

    Raises ``ValueError`` for syntactically invalid expressions (including
    out-of-range field values). Returns ``None`` only when a valid expression
    has no match within the search horizon.
    """

    if not cron or not str(cron).strip():
        return None
    expression = str(cron).strip()
    tz = ZoneInfo(timezone_name)
    local_after = after.astimezone(tz)

    if expression.lower().startswith("every:"):
        minutes = _parse_positive_int(
            expression.split(":", 1)[1],
            name="every minutes",
        )
        return local_after + timedelta(minutes=minutes)

    parsed = _parse_cron_expression(expression)
    return _next_calendar_match(parsed, local_after=local_after, tz=tz)


def _parse_cron_expression(expression: str) -> _CronFields:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(
            "cron must be 5 fields (min hour dom month dow) or every:<minutes>"
        )
    minute_f, hour_f, dom_f, month_f, dow_f = fields
    return _CronFields(
        minutes=_parse_field(minute_f, name="minute"),
        hours=_parse_field(hour_f, name="hour"),
        dom=_parse_field(dom_f, name="dom"),
        months=_parse_field(month_f, name="month"),
        dow=_parse_field(dow_f, name="dow"),
        dom_unrestricted=dom_f.strip() == "*",
        dow_unrestricted=dow_f.strip() == "*",
    )


def _parse_field(field: str, *, name: str) -> frozenset[int]:
    field = field.strip()
    if not field:
        raise ValueError(f"cron {name} field must not be empty")
    low, high = _FIELD_BOUNDS[name]
    if field == "*":
        values = set(range(low, high + 1))
    elif "," in field:
        values = set()
        for part in field.split(","):
            values.update(_parse_field(part, name=name))
        return frozenset(_normalize_dow(values) if name == "dow" else values)
    elif field.startswith("*/"):
        step = _parse_positive_int(field[2:], name=f"{name} step")
        values = {value for value in range(low, high + 1) if value % step == 0}
    elif "/" in field:
        range_part, step_s = field.split("/", 1)
        step = _parse_positive_int(step_s, name=f"{name} step")
        start, end = _parse_range_bounds(range_part, name=name, low=low, high=high)
        values = set(range(start, end + 1, step))
    elif "-" in field:
        start, end = _parse_range_bounds(field, name=name, low=low, high=high)
        values = set(range(start, end + 1))
    else:
        values = {_parse_bounded_int(field, name=name, low=low, high=high)}

    if name == "dow":
        values = _normalize_dow(values)
    if not values:
        raise ValueError(f"cron {name} field {field!r} matches no values")
    return frozenset(values)


def _parse_range_bounds(
    field: str,
    *,
    name: str,
    low: int,
    high: int,
) -> tuple[int, int]:
    if "-" not in field:
        raise ValueError(f"cron {name} range must look like START-END: {field!r}")
    start_s, end_s = field.split("-", 1)
    start = _parse_bounded_int(start_s, name=name, low=low, high=high)
    end = _parse_bounded_int(end_s, name=name, low=low, high=high)
    if start > end:
        raise ValueError(f"cron {name} range start must be <= end: {field!r}")
    return start, end


def _parse_bounded_int(raw: str, *, name: str, low: int, high: int) -> int:
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"cron {name} value must be an integer: {raw!r}") from exc
    if value < low or value > high:
        raise ValueError(f"cron {name} value {value} out of range {low}-{high}")
    return value


def _parse_positive_int(raw: str, *, name: str) -> int:
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"cron {name} must be an integer: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"cron {name} must be positive")
    return value


def _normalize_dow(values: set[int]) -> set[int]:
    # Cron accepts both 0 and 7 for Sunday; match against weekday() mapping 0–6.
    return {(0 if value == 7 else value) for value in values}


def _next_calendar_match(
    parsed: _CronFields,
    *,
    local_after: datetime,
    tz: ZoneInfo,
) -> datetime | None:
    """Walk matching calendar slots instead of every UTC minute (#101)."""

    start = (local_after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    limit = start.astimezone(UTC) + timedelta(days=_SEARCH_HORIZON_DAYS)
    after_utc = local_after.astimezone(UTC)
    minutes = sorted(parsed.minutes)
    hours = sorted(parsed.hours)
    months = sorted(parsed.months)
    start_tuple = (
        start.year,
        start.month,
        start.day,
        start.hour,
        start.minute,
    )
    max_year = start.year + (_SEARCH_HORIZON_DAYS // 365) + 2

    for year in range(start.year, max_year + 1):
        for month in months:
            if year == start.year and month < start.month:
                continue
            for day in _matching_days(year, month, parsed):
                if year == start.year and month == start.month and day < start.day:
                    continue
                for hour in hours:
                    for minute in minutes:
                        if (year, month, day, hour, minute) < start_tuple:
                            continue
                        for candidate in _local_candidates(
                            year, month, day, hour, minute, tz
                        ):
                            candidate_utc = candidate.astimezone(UTC)
                            if candidate_utc <= after_utc:
                                continue
                            if candidate_utc > limit:
                                return None
                            return candidate
    return None


def _local_candidates(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    tz: ZoneInfo,
) -> list[datetime]:
    """Return real wall times for this slot, both DST folds, in UTC order."""

    found: list[datetime] = []
    seen_utc: set[datetime] = set()
    for fold in (0, 1):
        candidate = datetime(year, month, day, hour, minute, tzinfo=tz, fold=fold)
        if not _is_real_local_wall_time(candidate):
            continue
        instant = candidate.astimezone(UTC)
        if instant in seen_utc:
            continue
        seen_utc.add(instant)
        found.append(candidate)
    found.sort(key=lambda item: item.astimezone(UTC))
    return found


def _matching_days(year: int, month: int, parsed: _CronFields) -> list[int]:
    last = calendar.monthrange(year, month)[1]
    days: list[int] = []
    for day in range(1, last + 1):
        cron_dow = (calendar.weekday(year, month, day) + 1) % 7
        if _dom_dow_matches(day=day, cron_dow=cron_dow, parsed=parsed):
            days.append(day)
    return days


def _dom_dow_matches(*, day: int, cron_dow: int, parsed: _CronFields) -> bool:
    dom_match = day in parsed.dom
    dow_match = cron_dow in parsed.dow
    if parsed.dom_unrestricted and parsed.dow_unrestricted:
        return True
    if parsed.dom_unrestricted:
        return dow_match
    if parsed.dow_unrestricted:
        return dom_match
    return dom_match or dow_match


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
        round_trip.fold,
    ) == (
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.fold,
    )
