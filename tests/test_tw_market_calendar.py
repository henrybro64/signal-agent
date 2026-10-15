from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core import (
    TwMarketCalendarError,
    clear_tw_market_holiday_cache,
    is_tw_market_calendar_year_covered,
    is_tw_market_holiday,
    latest_tw_market_calendar_year,
    load_tw_market_holiday_dates,
    tw_market_calendar_coverage_warning,
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
    assert tw_market_calendar_years(holidays) == frozenset({2026, 2027})
    assert is_tw_market_calendar_year_covered(2026, holiday_dates=holidays)
    assert is_tw_market_calendar_year_covered(2027, holiday_dates=holidays)
    assert date(2027, 2, 4) in holidays
    assert date(2027, 2, 10) in holidays
    assert not is_tw_market_calendar_year_covered(2028, holiday_dates=holidays)


def test_tw_market_calendar_coverage_warning_near_year_end() -> None:
    holidays = frozenset({date(2026, 1, 1), date(2026, 12, 25)})
    assert latest_tw_market_calendar_year(holidays) == 2026
    assert (
        tw_market_calendar_coverage_warning(
            today=date(2026, 6, 1),
            holiday_dates=holidays,
        )
        is None
    )
    near = tw_market_calendar_coverage_warning(
        today=date(2026, 11, 15),
        holiday_dates=holidays,
    )
    assert near is not None
    assert "2026" in near
    assert "2027" in near
    missing = tw_market_calendar_coverage_warning(
        today=date(2028, 1, 2),
        holiday_dates=holidays,
    )
    assert missing is not None
    assert "2028" in missing


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
