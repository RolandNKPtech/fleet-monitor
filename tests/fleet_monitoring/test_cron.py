"""Unit tests for the pure helpers in projects.fleet_monitoring.cron."""
from datetime import datetime, timedelta, timezone

import pytest

from projects.fleet_monitoring.cron import _parse_time, _next_fire


def test_parse_time_accepts_well_formed_input():
    assert _parse_time("00:00") == (0, 0)
    assert _parse_time("22:00") == (22, 0)
    assert _parse_time("23:59") == (23, 59)


@pytest.mark.parametrize("bad", ["", "22", "22:60", "24:00", "ab:cd", "1:2:3"])
def test_parse_time_rejects_garbage(bad):
    with pytest.raises(ValueError):
        _parse_time(bad)


def test_next_fire_returns_same_day_when_target_is_later():
    now = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
    assert _next_fire(now, 22, 0) == datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc)


def test_next_fire_rolls_forward_a_day_when_target_already_passed():
    now = datetime(2026, 5, 19, 23, 0, tzinfo=timezone.utc)
    assert _next_fire(now, 22, 0) == datetime(2026, 5, 20, 22, 0, tzinfo=timezone.utc)


def test_next_fire_handles_exact_match_by_rolling_forward():
    now = datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc)
    # If the target time is "now", we want the NEXT one (tomorrow), not zero wait.
    assert _next_fire(now, 22, 0) == datetime(2026, 5, 20, 22, 0, tzinfo=timezone.utc)
