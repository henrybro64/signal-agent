from __future__ import annotations

from datetime import UTC, date, datetime

from app.core import (
    TW_MARKET_SKIP_CALENDAR_UNAVAILABLE,
    TW_MARKET_SKIP_CLOSED,
    TW_MARKET_SKIP_HOLIDAY,
    is_tw_market_holiday,
    is_tw_market_open,
    tw_market_skip_reason,
)


def test_is_tw_market_open_returns_false_before_open() -> None:
    taipei_now = datetime.fromisoformat("2026-06-22T08:59:00+08:00")

    assert is_tw_market_open(taipei_now) is False
    assert tw_market_skip_reason(taipei_now) == TW_MARKET_SKIP_CLOSED


def test_is_tw_market_open_returns_true_at_open_boundary() -> None:
    now = datetime.fromisoformat("2026-06-22T09:00:00+08:00")

    assert is_tw_market_open(now) is True
    assert tw_market_skip_reason(now) is None


def test_is_tw_market_open_returns_true_during_market_hours() -> None:
    now = datetime.fromisoformat("2026-06-22T10:15:00+08:00")

    assert is_tw_market_open(now) is True


def test_is_tw_market_open_returns_true_at_close_boundary() -> None:
    now = datetime.fromisoformat("2026-06-22T13:30:00+08:00")

    assert is_tw_market_open(now) is True


def test_is_tw_market_open_returns_false_after_close() -> None:
    now = datetime.fromisoformat("2026-06-22T13:30:01+08:00")

    assert is_tw_market_open(now) is False


def test_is_tw_market_open_returns_false_on_weekends() -> None:
    sunday = datetime.fromisoformat("2026-06-21T10:00:00+08:00")
    saturday = datetime.fromisoformat("2026-06-20T10:00:00+08:00")

    assert is_tw_market_open(sunday) is False
    assert is_tw_market_open(saturday) is False
    assert tw_market_skip_reason(sunday) == TW_MARKET_SKIP_CLOSED


def test_is_tw_market_open_treats_naive_datetimes_as_taipei_local() -> None:
    before_open = datetime(2026, 6, 22, 8, 59)
    during_open = datetime(2026, 6, 22, 10, 0)

    assert is_tw_market_open(before_open) is False
    assert is_tw_market_open(during_open) is True


def test_is_tw_market_open_converts_non_taipei_timezones() -> None:
    now = datetime(2026, 6, 22, 1, 0, tzinfo=UTC)

    assert is_tw_market_open(now) is True


def test_is_tw_market_open_returns_false_on_configured_holiday() -> None:
    # 2026-01-01 is a Thursday and within market hours, but a TWSE holiday.
    holiday_open_hours = datetime.fromisoformat("2026-01-01T10:00:00+08:00")
    holiday_naive = datetime(2026, 1, 1, 10, 0)

    assert is_tw_market_holiday(date(2026, 1, 1)) is True
    assert is_tw_market_open(holiday_open_hours) is False
    assert is_tw_market_open(holiday_naive) is False
    assert tw_market_skip_reason(holiday_open_hours) == TW_MARKET_SKIP_HOLIDAY


def test_is_tw_market_open_returns_false_on_settlement_only_day() -> None:
    # 2026-02-12 is Thursday settlement-only (no trading).
    settlement_day = datetime.fromisoformat("2026-02-12T10:00:00+08:00")

    assert is_tw_market_holiday(date(2026, 2, 12)) is True
    assert is_tw_market_open(settlement_day) is False
    assert tw_market_skip_reason(settlement_day) == TW_MARKET_SKIP_HOLIDAY


def test_is_tw_market_open_returns_false_on_announced_typhoon_closure() -> None:
    typhoon_day = datetime.fromisoformat("2026-07-10T10:00:00+08:00")

    assert is_tw_market_holiday(date(2026, 7, 10)) is True
    assert is_tw_market_open(typhoon_day) is False
    assert tw_market_skip_reason(typhoon_day) == TW_MARKET_SKIP_HOLIDAY


def test_is_tw_market_open_fails_closed_when_year_not_in_calendar() -> None:
    # Packaged YAML currently covers 2026 only; uncovered years must not look open.
    uncovered = datetime.fromisoformat("2027-01-04T10:00:00+08:00")

    assert is_tw_market_open(uncovered) is False
    assert tw_market_skip_reason(uncovered) == TW_MARKET_SKIP_CALENDAR_UNAVAILABLE


def test_is_tw_market_holiday_accepts_datetime() -> None:
    assert is_tw_market_holiday(datetime(2026, 5, 1, 12, 0)) is True
    assert is_tw_market_holiday(datetime(2026, 6, 22, 12, 0)) is False
