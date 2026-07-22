from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core import (
    TwMarketCalendarError,
    clear_tw_market_holiday_cache,
    is_tw_market_calendar_year_covered,
    is_tw_market_holiday,
    load_tw_market_holiday_dates,
    tw_market_calendar_years,
)


def test_load_packaged_tw_market_holiday_dates_includes_2026_entries() -> None:
    clear_tw_market_holiday_cache()
    holidays = load_tw_market_holiday_dates()

    assert date(2026, 1, 1) in holidays
    assert date(2026, 6, 19) in holidays
    assert date(2026, 7, 10) in holidays
    assert date(2026, 12, 25) in holidays
    # Trading-day markers must not be treated as holidays.
    assert date(2026, 1, 2) not in holidays
    assert date(2026, 2, 11) not in holidays
    assert date(2026, 2, 23) not in holidays
    assert tw_market_calendar_years(holidays) == frozenset({2026})
    assert is_tw_market_calendar_year_covered(2026, holiday_dates=holidays)
    assert not is_tw_market_calendar_year_covered(2027, holiday_dates=holidays)


def test_load_tw_market_holiday_dates_from_custom_file(tmp_path: Path) -> None:
    holiday_file = tmp_path / "holidays.yaml"
    holiday_file.write_text(
        "\n".join(
            [
                "holidays:",
                "  - date: 2027-01-01",
                "    name: 元旦",
                "  - date: 2027-02-16",
                "    name: 春節休市",
                "",
            ]
        ),
        encoding="utf-8",
    )

    holidays = load_tw_market_holiday_dates(holiday_file)

    assert holidays == frozenset({date(2027, 1, 1), date(2027, 2, 16)})
    assert is_tw_market_holiday(date(2027, 1, 1), holiday_dates=holidays) is True
    assert is_tw_market_holiday(date(2027, 1, 2), holiday_dates=holidays) is False


def test_load_tw_market_holiday_dates_rejects_invalid_yaml(tmp_path: Path) -> None:
    holiday_file = tmp_path / "bad.yaml"
    holiday_file.write_text("holidays: [\n", encoding="utf-8")

    with pytest.raises(TwMarketCalendarError, match="Invalid YAML"):
        load_tw_market_holiday_dates(holiday_file)


def test_load_tw_market_holiday_dates_rejects_missing_holidays_key(
    tmp_path: Path,
) -> None:
    holiday_file = tmp_path / "missing.yaml"
    holiday_file.write_text("years: []\n", encoding="utf-8")

    with pytest.raises(TwMarketCalendarError, match="missing 'holidays'"):
        load_tw_market_holiday_dates(holiday_file)


def test_load_tw_market_holiday_dates_rejects_bad_date(tmp_path: Path) -> None:
    holiday_file = tmp_path / "bad-date.yaml"
    holiday_file.write_text(
        "\n".join(
            [
                "holidays:",
                "  - date: not-a-date",
                "    name: broken",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TwMarketCalendarError, match="Invalid holiday date"):
        load_tw_market_holiday_dates(holiday_file)
