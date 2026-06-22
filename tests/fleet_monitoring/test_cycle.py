from datetime import date
import pytest
from projects.fleet_monitoring.cycle import cycle_window


def test_cycle_window_mid_cycle():
    # Today is 2026-05-19; cycle starts on the 13th of each month.
    start, end, day_n, length = cycle_window(date(2026, 5, 19), 13)
    assert start == date(2026, 5, 13)
    assert end == date(2026, 6, 13)
    assert day_n == 7                  # 13, 14, 15, 16, 17, 18, 19 = 7 days inclusive
    assert length == 31                # May has 31 days; cycle is May 13 -> Jun 13


def test_cycle_window_first_day_of_cycle():
    start, end, day_n, length = cycle_window(date(2026, 5, 13), 13)
    assert start == date(2026, 5, 13)
    assert end == date(2026, 6, 13)
    assert day_n == 1


def test_cycle_window_last_day_of_cycle():
    # Day before the next cycle starts.
    start, end, day_n, length = cycle_window(date(2026, 6, 12), 13)
    assert start == date(2026, 5, 13)
    assert end == date(2026, 6, 13)
    assert day_n == 31


def test_cycle_window_today_before_cycle_start_day_of_month():
    # Today is May 5 with cycle_start_day=13 -> we are inside the April 13 -> May 13 cycle.
    start, end, day_n, length = cycle_window(date(2026, 5, 5), 13)
    assert start == date(2026, 4, 13)
    assert end == date(2026, 5, 13)
    assert day_n == 23                 # April 13 -> May 5 inclusive


def test_cycle_window_day_31_in_30_day_month_clamps():
    # cycle_start_day=31 in April (30 days) -> clamps to April 30.
    start, end, day_n, length = cycle_window(date(2026, 5, 5), 31)
    assert start == date(2026, 4, 30)
    assert end == date(2026, 5, 31)
    assert day_n == 6


def test_cycle_window_leap_year_february():
    # cycle_start_day=29 in non-leap Feb clamps to Feb 28.
    start, end, day_n, length = cycle_window(date(2025, 3, 5), 29)
    assert start == date(2025, 2, 28)


def test_cycle_window_first_of_month():
    # cycle_start_day=1 -> calendar-month cycle.
    start, end, day_n, length = cycle_window(date(2026, 5, 19), 1)
    assert start == date(2026, 5, 1)
    assert end == date(2026, 6, 1)
    assert day_n == 19
    assert length == 31


def test_cycle_window_rejects_invalid_day():
    with pytest.raises(ValueError):
        cycle_window(date(2026, 5, 19), 0)
    with pytest.raises(ValueError):
        cycle_window(date(2026, 5, 19), 32)


from projects.fleet_monitoring.cycle import (
    cycle_to_date_gb, cycle_to_date_visits)


def _daily(account: str, date_str: str, total_bytes: int, billable: int = 0) -> dict:
    return {"date": date_str, "account": account, "install": "x",
            "network_total_bytes": total_bytes, "billable_visits": billable}


def test_cycle_to_date_gb_sums_only_account_and_window():
    rows = [
        _daily("acctF", "2026-05-13", 1_000_000_000),     # cycle start
        _daily("acctF", "2026-05-14", 2_000_000_000),
        _daily("acctF", "2026-05-19",   500_000_000),     # today
        _daily("acctF", "2026-05-12", 9_000_000_000),     # PRIOR cycle, excluded
        _daily("acctF", "2026-05-20", 9_000_000_000),     # future, excluded
        _daily("acctA", "2026-05-15", 8_000_000_000),     # other account, excluded
    ]
    gb = cycle_to_date_gb(
        "acctF", rows,
        cycle_start=date(2026, 5, 13), today=date(2026, 5, 19))
    assert gb == 3.5                # 1.0 + 2.0 + 0.5 = 3.5 GB


def test_cycle_to_date_visits_sums():
    rows = [
        _daily("acctF", "2026-05-13", 0, billable=100),
        _daily("acctF", "2026-05-14", 0, billable=200),
        _daily("acctF", "2026-05-12", 0, billable=999),    # prior, excluded
    ]
    v = cycle_to_date_visits(
        "acctF", rows,
        cycle_start=date(2026, 5, 13), today=date(2026, 5, 19))
    assert v == 300


def test_cycle_to_date_handles_missing_fields():
    # Rows with null bytes/visits don't blow up; just contribute zero.
    rows = [
        {"date": "2026-05-13", "account": "x", "install": "y",
         "network_total_bytes": None, "billable_visits": None},
        {"date": "2026-05-14", "account": "x", "install": "y",
         "network_total_bytes": 1_000_000_000, "billable_visits": 50},
    ]
    assert cycle_to_date_gb("x", rows, date(2026, 5, 13), date(2026, 5, 14)) == 1.0
    assert cycle_to_date_visits("x", rows, date(2026, 5, 13), date(2026, 5, 14)) == 50
