"""Tests for organic_traffic_drop: GSC clicks WoW collapse."""
from projects.fleet_monitoring.rules import organic_traffic_drop


def _site(c7=None, c_prev=None):
    gsc = {"clicks_7d": c7, "clicks_prev_7d": c_prev} \
        if c7 is not None else None
    return {"key": "a.com", "analytics": {"ga4": None, "gsc": gsc}}


def test_fires_critical_when_clicks_drop_50_percent_or_more():
    site = _site(c7=200, c_prev=500)   # -60%
    alerts = organic_traffic_drop.evaluate(site, history=[])
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    assert alerts[0].rule == "organic_traffic_drop"


def test_fires_warning_when_clicks_drop_30_to_50_percent():
    site = _site(c7=300, c_prev=500)   # -40%
    alerts = organic_traffic_drop.evaluate(site, history=[])
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"


def test_does_not_fire_below_30_percent_drop():
    site = _site(c7=400, c_prev=500)   # -20%
    assert organic_traffic_drop.evaluate(site, history=[]) == []


def test_skips_tiny_baseline():
    site = _site(c7=5, c_prev=20)
    assert organic_traffic_drop.evaluate(site, history=[]) == []


def test_skips_when_gsc_block_is_none():
    assert organic_traffic_drop.evaluate(_site(c7=None), history=[]) == []


def test_registered_in_rules_registry():
    from projects.fleet_monitoring import rules
    assert organic_traffic_drop in rules.REGISTRY
