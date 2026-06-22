"""Billing-cycle window math — pure functions, no I/O.

Each WPE account has a billing anniversary (1-31 day of month). The cycle
runs from cycle_start_day of one month to cycle_start_day of the next,
exclusive on the end. When cycle_start_day exceeds the month's length, it
clamps to the last day of that month (e.g. day 31 in April -> April 30).
"""
from __future__ import annotations
import calendar
from datetime import date


def _clamp_to_month(year: int, month: int, day: int) -> date:
    """Return date(year, month, day) clamped to that month's last day."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def cycle_window(today: date, cycle_start_day: int) -> tuple[date, date, int, int]:
    """Return (cycle_start, cycle_end_exclusive, day_n_1_based, cycle_length_days).

    `cycle_start_day` is the day-of-month each billing cycle begins (1-31).
    Values above the month's last day clamp to the last day.

    `cycle_end_exclusive` is the NEXT cycle's start date, so summing days
    in [cycle_start, cycle_end_exclusive) gives the full cycle.
    `day_n` is 1 on cycle_start and increments each day through the cycle.
    """
    if not (1 <= cycle_start_day <= 31):
        raise ValueError(
            f"cycle_start_day must be in 1..31, got {cycle_start_day!r}")

    # Anchor month: which month's `cycle_start_day` is the start of the cycle
    # containing `today`?
    candidate_start = _clamp_to_month(today.year, today.month, cycle_start_day)
    if today >= candidate_start:
        cycle_start = candidate_start
        next_year = today.year + (1 if today.month == 12 else 0)
        next_month = 1 if today.month == 12 else today.month + 1
        cycle_end = _clamp_to_month(next_year, next_month, cycle_start_day)
    else:
        # Today falls before this month's clamped start; the cycle began
        # in the prior month.
        prev_year = today.year - (1 if today.month == 1 else 0)
        prev_month = 12 if today.month == 1 else today.month - 1
        cycle_start = _clamp_to_month(prev_year, prev_month, cycle_start_day)
        cycle_end = candidate_start

    day_n = (today - cycle_start).days + 1
    cycle_length = (cycle_end - cycle_start).days
    return cycle_start, cycle_end, day_n, cycle_length


def cycle_to_date_gb(account: str, daily_rows: list[dict],
                     cycle_start: date, today: date) -> float:
    """Sum of network_total_bytes / 1e9 for `account` over [cycle_start, today]."""
    total_bytes = 0
    for r in daily_rows:
        if r.get("account") != account:
            continue
        try:
            row_date = date.fromisoformat(r["date"])
        except (KeyError, ValueError):
            continue
        if row_date < cycle_start or row_date > today:
            continue
        total_bytes += int(r.get("network_total_bytes") or 0)
    return round(total_bytes / 1e9, 2)


def cycle_to_date_visits(account: str, daily_rows: list[dict],
                         cycle_start: date, today: date) -> int:
    """Sum of billable_visits for `account` over [cycle_start, today]."""
    total = 0
    for r in daily_rows:
        if r.get("account") != account:
            continue
        try:
            row_date = date.fromisoformat(r["date"])
        except (KeyError, ValueError):
            continue
        if row_date < cycle_start or row_date > today:
            continue
        total += int(r.get("billable_visits") or 0)
    return total
