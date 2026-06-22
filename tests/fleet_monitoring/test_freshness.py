"""Tests for the freshness 3-state pill helper."""
from datetime import datetime, timedelta, timezone
from unittest import mock
from projects.fleet_monitoring import models


def _now_minus(hours):
    """Return an ISO-8601 captured_at for `hours` ago."""
    when = datetime.now(timezone.utc) - timedelta(hours=hours)
    return when.isoformat()


def test_fresh_for_recent_snapshots():
    label, cls = models.freshness(_now_minus(2))
    assert cls == "fresh"
    assert "fresh" in label
    assert "2h ago" in label or "1h ago" in label   # allow rounding slop


def test_fresh_at_exactly_30h_boundary():
    label, cls = models.freshness(_now_minus(30))
    assert cls == "fresh"


def test_aging_just_over_fresh_threshold():
    label, cls = models.freshness(_now_minus(36))
    assert cls == "aging"
    assert "aging" in label


def test_aging_at_48h_boundary():
    label, cls = models.freshness(_now_minus(48))
    assert cls == "aging"


def test_stale_past_48h():
    label, cls = models.freshness(_now_minus(72))
    assert cls == "stale"
    assert "STALE" in label   # uppercase to grab the eye


def test_unparseable_input_treated_as_stale():
    label, cls = models.freshness("garbage")
    assert cls == "stale"
    label2, cls2 = models.freshness("")
    assert cls2 == "stale"


def test_handles_zulu_timezone_suffix():
    when = (datetime.now(timezone.utc) - timedelta(hours=5)
            ).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    label, cls = models.freshness(when)
    assert cls == "fresh"


def test_sub_hour_age_shows_minutes():
    label, cls = models.freshness(_now_minus(0.25))   # 15m
    assert "m ago" in label
    assert cls == "fresh"
