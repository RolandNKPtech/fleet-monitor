from projects.fleet_monitoring.rules import config_drift, hard_thresholds


def test_config_drift_fires_per_change_with_severity():
    prev = {"cf": {"config": {"settings": {"ssl": "strict", "min_tls_version": "1.2"},
                              "bot": {}, "waf_rules": [], "cache_rules": [],
                              "dns_proxy_apex": None, "dns_proxy_www": True}}}
    site = {"key": "x.com",
            "cf": {"config": {"settings": {"ssl": "full", "min_tls_version": "1.2"},
                              "bot": {}, "waf_rules": [], "cache_rules": [],
                              "dns_proxy_apex": None, "dns_proxy_www": True}}}
    alerts = config_drift.evaluate(site, [prev], rule_changes=[])
    assert len(alerts) == 1
    assert alerts[0].rule == "config_drift"
    assert alerts[0].severity == "critical"          # ssl downgrade
    assert alerts[0].detail["attribution"] == "external"   # no matching log entry


def test_config_drift_attributes_our_own_changes():
    from datetime import datetime, timedelta, timezone
    prev = {"cf": {"config": {"settings": {"ssl": "strict"}, "bot": {}, "waf_rules": [],
                              "cache_rules": [], "dns_proxy_apex": None, "dns_proxy_www": None}}}
    site = {"key": "x.com",
            "cf": {"config": {"settings": {"ssl": "full"}, "bot": {}, "waf_rules": [],
                              "cache_rules": [], "dns_proxy_apex": None, "dns_proxy_www": None}}}
    # Relative timestamp so the test doesn't bit-rot once wall-clock passes the
    # 48h ATTRIBUTION_WINDOW_H in rules/config_drift.py.
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    rule_changes = [{"domain": "x.com", "timestamp": recent}]
    alerts = config_drift.evaluate(site, [prev], rule_changes=rule_changes)
    assert alerts[0].detail["attribution"] == "us"


def test_hard_thresholds_flag_insecure_tls():
    site = {"key": "x.com", "cf": {"config": {"settings": {"min_tls_version": "1.0"}}}}
    alerts = hard_thresholds.evaluate(site, [])
    assert any(a.rule == "insecure_tls" for a in alerts)
    assert all(a.severity == "critical" for a in alerts)
