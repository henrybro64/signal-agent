from __future__ import annotations

from datetime import UTC, datetime

from app.core import is_tw_market_open


def test_is_tw_market_open_returns_false_before_open() -> None:
    taipei_now = datetime.fromisoformat("2026-06-22T08:59:00+08:00")

    assert is_tw_market_open(taipei_now) is False


def test_is_tw_market_open_returns_true_at_open_boundary() -> None:
    now = datetime.fromisoformat("2026-06-22T09:00:00+08:00")

    assert is_tw_market_open(now) is True


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


def test_is_tw_market_open_treats_naive_datetimes_as_taipei_local() -> None:
    before_open = datetime(2026, 6, 22, 8, 59)
    during_open = datetime(2026, 6, 22, 10, 0)

    assert is_tw_market_open(before_open) is False
    assert is_tw_market_open(during_open) is True


def test_is_tw_market_open_converts_non_taipei_timezones() -> None:
    now = datetime(2026, 6, 22, 1, 0, tzinfo=UTC)

    assert is_tw_market_open(now) is True
