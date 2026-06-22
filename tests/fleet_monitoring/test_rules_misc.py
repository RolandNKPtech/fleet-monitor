from projects.fleet_monitoring.rules import (probe_failure, new_offender,
                                             fix_regression, collection_gap)


def test_probe_failure_fires_when_allowlisted_ua_blocked():
    site = {"key": "x.com", "probe": {
        "GPTBot": {"http": 403, "expected": 200, "ok": False},
        "Bytespider": {"http": 403, "expected": 403, "ok": True},
    }}
    alerts = probe_failure.evaluate(site, [])
    assert len(alerts) == 1 and alerts[0].rule == "probe_failure"
    assert "GPTBot" in alerts[0].summary


def test_new_offender_fires_for_unmanaged_bot_signature():
    site = {"key": "x.com", "overlay": None,
            "wpe": {"mb_per_visit": 45, "bandwidth_gb_30d": 60}}
    alerts = new_offender.evaluate(site, [])
    assert len(alerts) == 1 and alerts[0].rule == "new_offender"


def test_new_offender_silent_for_managed_site():
    site = {"key": "x.com", "overlay": {"fixed": True},
            "wpe": {"mb_per_visit": 45, "bandwidth_gb_30d": 60}}
    assert new_offender.evaluate(site, []) == []


def test_fix_regression_fires_when_fixed_site_climbs_back():
    site = {"key": "x.com",
            "overlay": {"fixed": True, "pre_fix_bandwidth_gb_30d": 100},
            "wpe": {"bandwidth_gb_30d": 92}}        # within 10% of pre-fix
    alerts = fix_regression.evaluate(site, [])
    assert len(alerts) == 1 and alerts[0].severity == "critical"


def test_collection_gap_fires_when_expected_data_missing():
    site = {"key": "x.com", "join_state": "wpe+cf", "wpe": None,
            "cf": {"config": {"error": "boom"}}}
    alerts = collection_gap.evaluate(site, [])
    assert len(alerts) >= 1 and all(a.rule == "collection_gap" for a in alerts)
