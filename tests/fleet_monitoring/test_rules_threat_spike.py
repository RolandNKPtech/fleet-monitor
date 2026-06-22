"""Tests for threat_spike: CF 30d threat total growing sharply vs baseline."""
from projects.fleet_monitoring.rules import threat_spike


def _site(threats_30d):
    return {"key": "a.com",
            "cf": {"analytics": {"threats": threats_30d}}}


def _history(threats_list):
    """Each entry is a prior snapshot's per-site cf.analytics.threats."""
    return [{"cf": {"analytics": {"threats": t}}} for t in threats_list]


_BASELINE = [1_000, 1_200, 1_100, 1_300, 1_150, 1_050, 1_250]   # 7 snapshots, median 1,150


def test_fires_critical_when_threats_doubled_vs_baseline():
    site = _site(threats_30d=3_000)   # ~2.6x median (1,150)
    alerts = threat_spike.evaluate(site, _history(_BASELINE))
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    assert alerts[0].rule == "threat_spike"
    assert alerts[0].fingerprint() == "a.com:threat_spike"


def test_fires_warning_when_threats_grew_50_to_100_percent():
    site = _site(threats_30d=1_800)   # ~1.57x median — between warn and crit
    alerts = threat_spike.evaluate(site, _history(_BASELINE))
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"


def test_does_not_fire_below_50_percent_growth():
    site = _site(threats_30d=1_500)   # ~1.3x median — sub-threshold
    assert threat_spike.evaluate(site, _history(_BASELINE)) == []


def test_skips_when_below_min_threats_floor():
    # Even with 5x growth, tiny counts are noise (firewall events on a quiet site)
    site = _site(threats_30d=50)
    assert threat_spike.evaluate(site, _history([10, 12, 8, 11, 9, 13, 10])) == []


def test_skips_when_history_is_empty():
    site = _site(threats_30d=5_000)
    assert threat_spike.evaluate(site, _history([])) == []


def test_skips_when_current_threats_is_none():
    site = {"key": "a.com", "cf": {"analytics": {}}}
    assert threat_spike.evaluate(site, _history([1_000])) == []


def test_registered_in_rules_registry():
    from projects.fleet_monitoring import rules
    assert threat_spike in rules.REGISTRY


def test_bot_ratio_legacy_is_no_longer_registered():
    """The heuristic bot_ratio (WPE billable proxy) was retired once bot_ratio_real
    proved itself — Plan 2 retirement note. The module itself still exists (it can
    be revived) but it should not fire any more."""
    from projects.fleet_monitoring import rules
    from projects.fleet_monitoring.rules import bot_ratio
    assert bot_ratio not in rules.REGISTRY
