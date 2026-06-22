"""Tests for tracking_failure: GA4 collapses while GSC holds."""
from projects.fleet_monitoring.rules import tracking_failure


def _site(s7=None, s_prev=None, c7=None, c_prev=None):
    ga4 = {"sessions_7d": s7, "sessions_prev_7d": s_prev} \
        if s7 is not None else None
    gsc = {"clicks_7d": c7, "clicks_prev_7d": c_prev} \
        if c7 is not None else None
    return {"key": "a.com",
            "analytics": {"ga4": ga4, "gsc": gsc}}


def test_fires_when_ga4_collapses_and_gsc_holds():
    # GA4 -50% (5400 -> 2700), GSC -0%  -> tracking-break suspect
    site = _site(s7=2700, s_prev=5400, c7=300, c_prev=300)
    alerts = tracking_failure.evaluate(site, history=[])
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].rule == "tracking_failure"


def test_does_not_fire_when_ga4_drop_below_30_percent():
    site = _site(s7=4500, s_prev=5000, c7=300, c_prev=300)   # GA4 -10%
    assert tracking_failure.evaluate(site, history=[]) == []


def test_does_not_fire_when_gsc_also_collapsed():
    site = _site(s7=2700, s_prev=5400, c7=120, c_prev=300)   # both halved
    assert tracking_failure.evaluate(site, history=[]) == []


def test_skips_tiny_baselines():
    site = _site(s7=10, s_prev=20, c7=2, c_prev=10)
    assert tracking_failure.evaluate(site, history=[]) == []


def test_skips_when_either_block_is_none():
    site = _site(s7=2700, s_prev=5400, c7=None, c_prev=None)
    assert tracking_failure.evaluate(site, history=[]) == []


def test_registered_in_rules_registry():
    from projects.fleet_monitoring import rules
    assert tracking_failure in rules.REGISTRY
