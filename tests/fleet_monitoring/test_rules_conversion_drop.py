"""Tests for conversion_drop: GA4 conversion-event WoW collapse."""
from projects.fleet_monitoring.rules import conversion_drop


def _site(c7=None, c_prev=None):
    ga4 = {"conversions_7d": c7, "conversions_prev_7d": c_prev} \
        if c7 is not None else None
    return {"key": "a.com", "analytics": {"ga4": ga4, "gsc": None}}


def test_fires_critical_when_conversions_halved():
    site = _site(c7=5, c_prev=20)   # -75%
    alerts = conversion_drop.evaluate(site, history=[])
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    assert alerts[0].rule == "conversion_drop"


def test_does_not_fire_below_50_percent_drop():
    site = _site(c7=12, c_prev=20)   # -40%
    assert conversion_drop.evaluate(site, history=[]) == []


def test_skips_tiny_baseline():
    site = _site(c7=0, c_prev=2)
    assert conversion_drop.evaluate(site, history=[]) == []


def test_skips_when_ga4_block_is_none():
    assert conversion_drop.evaluate(_site(c7=None), history=[]) == []


def test_registered_in_rules_registry():
    from projects.fleet_monitoring import rules
    assert conversion_drop in rules.REGISTRY
