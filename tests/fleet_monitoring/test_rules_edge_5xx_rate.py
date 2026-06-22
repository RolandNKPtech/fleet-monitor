"""Tests for edge_5xx_rate: edge 5xx percentage over the last 7 calendar days."""
from projects.fleet_monitoring.rules import edge_5xx_rate


def _site(pct_5xx_7d, requests_7d=10_000, requests_5xx_7d=None, top=None):
    if requests_5xx_7d is None and pct_5xx_7d is not None:
        requests_5xx_7d = int(round(requests_7d * pct_5xx_7d / 100))
    return {
        "key": "a.com",
        "cf": {"analytics": {
            "requests_7d": requests_7d,
            "requests_5xx_7d": requests_5xx_7d or 0,
            "pct_5xx_7d": pct_5xx_7d,
            "top_status_codes_7d": top or [
                {"code": 200, "requests": 9_500},
                {"code": 522, "requests": 300},
                {"code": 500, "requests": 200},
            ],
        }},
    }


def test_fires_warning_when_5xx_between_1_and_3_percent():
    alerts = edge_5xx_rate.evaluate(_site(pct_5xx_7d=1.5), [])
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].rule == "edge_5xx_rate"
    assert alerts[0].fingerprint() == "a.com:edge_5xx_rate"


def test_fires_critical_when_5xx_above_3_percent():
    alerts = edge_5xx_rate.evaluate(_site(pct_5xx_7d=5.2), [])
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"


def test_does_not_fire_below_1_percent():
    assert edge_5xx_rate.evaluate(_site(pct_5xx_7d=0.4), []) == []


def test_skips_when_requests_below_volume_floor():
    # 8% 5xx over 500 requests — below MIN_REQUESTS_7D=1000
    s = _site(pct_5xx_7d=8.0, requests_7d=500, requests_5xx_7d=40)
    assert edge_5xx_rate.evaluate(s, []) == []


def test_fires_on_small_site_above_min_requests_floor():
    # 1500 requests, 18 5xx (1.2%) — above both floors
    s = _site(pct_5xx_7d=1.2, requests_7d=1500, requests_5xx_7d=18)
    alerts = edge_5xx_rate.evaluate(s, [])
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"


def test_skips_when_below_min_5xx_event_floor_even_at_high_pct():
    # 1200 requests, 8 5xx (0.67%) — request floor passes but absolute 5xx < 10
    s = _site(pct_5xx_7d=0.67, requests_7d=1200, requests_5xx_7d=8)
    assert edge_5xx_rate.evaluate(s, []) == []


def test_skips_when_pct_5xx_is_none():
    s = {"key": "a.com", "cf": {"analytics": {"requests_7d": 50_000}}}
    assert edge_5xx_rate.evaluate(s, []) == []


def test_skips_when_cf_analytics_missing():
    assert edge_5xx_rate.evaluate({"key": "a.com", "cf": {}}, []) == []


def test_summary_uses_7d_top_codes_not_30d():
    s = _site(pct_5xx_7d=2.0, requests_7d=10_000, top=[
        {"code": 200, "requests": 9_500},
        {"code": 522, "requests": 250},
        {"code": 500, "requests": 150},
        {"code": 404, "requests": 100},
    ])
    alerts = edge_5xx_rate.evaluate(s, [])
    assert "522=250" in alerts[0].summary
    assert "500=150" in alerts[0].summary
    assert "404" not in alerts[0].summary   # 4xx must not appear in 5xx breakdown
    # Summary must NOT mention 30d — that was the mixed-window bug.
    assert "30d" not in alerts[0].summary
    assert "7d" in alerts[0].summary


def test_detail_has_only_7d_fields():
    s = _site(pct_5xx_7d=2.0)
    alert = edge_5xx_rate.evaluate(s, [])[0]
    assert "pct_5xx_30d" not in alert.detail
    assert "top_5xx_codes_30d" not in alert.detail
    assert "top_5xx_codes_7d" in alert.detail


def test_registered_in_rules_registry():
    from projects.fleet_monitoring import rules
    assert edge_5xx_rate in rules.REGISTRY


def test_legacy_origin_5xx_rate_no_longer_imports():
    # The old rule module was renamed; the new module is the canonical name.
    import projects.fleet_monitoring.rules as rules_pkg
    assert not hasattr(rules_pkg, "origin_5xx_rate")
    assert hasattr(rules_pkg, "edge_5xx_rate")
