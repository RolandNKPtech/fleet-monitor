"""Tests for the bot_ratio_real rule (CF requests vs GA4 sessions)."""
from projects.fleet_monitoring.rules import bot_ratio_real


def _site(requests_30d=None, sessions_30d=None):
    cf = {"analytics": {"requests_30d": requests_30d}}
    ga4 = {"sessions_30d": sessions_30d} if sessions_30d is not None else None
    return {"key": "a.com", "cf": cf,
            "analytics": {"ga4": ga4, "gsc": None}}


def test_fires_critical_when_real_sessions_below_5_percent():
    site = _site(requests_30d=10_000, sessions_30d=400)   # 4% real
    alerts = bot_ratio_real.evaluate(site, history=[])
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    assert alerts[0].rule == "bot_ratio_real"
    assert alerts[0].fingerprint() == "a.com:bot_ratio_real"


def test_fires_warning_when_real_sessions_5_to_10_percent():
    site = _site(requests_30d=10_000, sessions_30d=800)   # 8% real
    alerts = bot_ratio_real.evaluate(site, history=[])
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"


def test_does_not_fire_when_real_sessions_above_10_percent():
    site = _site(requests_30d=10_000, sessions_30d=2000)  # 20% real
    assert bot_ratio_real.evaluate(site, history=[]) == []


def test_skips_when_requests_below_min_threshold():
    site = _site(requests_30d=500, sessions_30d=10)       # tiny site -> skip
    assert bot_ratio_real.evaluate(site, history=[]) == []


def test_skips_when_ga4_block_is_none():
    site = _site(requests_30d=10_000, sessions_30d=None)
    assert bot_ratio_real.evaluate(site, history=[]) == []


def test_registered_in_rules_registry():
    from projects.fleet_monitoring import rules
    assert bot_ratio_real in rules.REGISTRY
