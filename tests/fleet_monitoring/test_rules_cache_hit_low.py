"""Tests for cache_hit_low rule."""
from projects.fleet_monitoring.rules import cache_hit_low


def _site(cache_hit_rate, requests_30d=100_000):
    return {
        "key": "a.com",
        "cf": {"analytics": {
            "cache_hit_rate": cache_hit_rate,
            "requests_30d": requests_30d,
        }},
    }


def test_fires_warning_when_cache_between_50_and_70():
    alerts = cache_hit_low.evaluate(_site(cache_hit_rate=65.0), [])
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].rule == "cache_hit_low"
    assert "under-cached" in alerts[0].summary


def test_fires_critical_when_cache_below_50():
    alerts = cache_hit_low.evaluate(_site(cache_hit_rate=34.5), [])
    assert alerts[0].severity == "critical"
    assert "broken cache" in alerts[0].summary


def test_does_not_fire_at_or_above_70():
    assert cache_hit_low.evaluate(_site(cache_hit_rate=70.0), []) == []
    assert cache_hit_low.evaluate(_site(cache_hit_rate=88.0), []) == []


def test_skips_when_requests_below_volume_floor():
    # Tiny site at 30% cache hit is noise, not a real misconfig
    s = _site(cache_hit_rate=30.0, requests_30d=500)
    assert cache_hit_low.evaluate(s, []) == []


def test_skips_when_cache_hit_rate_is_none():
    s = {"key": "a.com", "cf": {"analytics": {"requests_30d": 100_000}}}
    assert cache_hit_low.evaluate(s, []) == []


def test_skips_when_cf_analytics_missing():
    assert cache_hit_low.evaluate({"key": "a.com", "cf": {}}, []) == []


def test_fingerprint_stable_for_lifecycle():
    a = cache_hit_low.evaluate(_site(cache_hit_rate=40.0), [])[0]
    assert a.fingerprint() == "a.com:cache_hit_low"


def test_registered_in_rules_registry():
    from projects.fleet_monitoring import rules
    assert cache_hit_low in rules.REGISTRY
