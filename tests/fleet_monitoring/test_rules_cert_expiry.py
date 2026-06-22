"""Tests for cert_expiry rule."""
from projects.fleet_monitoring.rules import cert_expiry


def _site(days, expires_on="2026-07-01", issuer="GoogleTrustServices",
          active_count=1):
    return {
        "key": "a.com",
        "cf": {"cert_expiry": {
            "min_days_until_expiry": days,
            "earliest_expires_on": expires_on,
            "earliest_issuer": issuer,
            "active_pack_count": active_count,
            "earliest_pack_id": "pack-1",
        }},
    }


def test_fires_warning_at_30_days_out():
    alerts = cert_expiry.evaluate(_site(days=30), [])
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].rule == "cert_expiry"
    assert "30 days" in alerts[0].summary


def test_fires_warning_for_days_between_14_and_30():
    alerts = cert_expiry.evaluate(_site(days=22), [])
    assert alerts[0].severity == "warning"


def test_fires_critical_at_14_days_out():
    alerts = cert_expiry.evaluate(_site(days=14), [])
    assert alerts[0].severity == "critical"


def test_fires_critical_for_days_between_1_and_14():
    alerts = cert_expiry.evaluate(_site(days=5), [])
    assert alerts[0].severity == "critical"
    assert "urgent" in alerts[0].summary


def test_fires_critical_when_expired_with_negative_days():
    alerts = cert_expiry.evaluate(_site(days=-3), [])
    assert alerts[0].severity == "critical"
    assert "EXPIRED" in alerts[0].summary
    assert "3 days ago" in alerts[0].summary


def test_does_not_fire_above_30_days():
    assert cert_expiry.evaluate(_site(days=31), []) == []
    assert cert_expiry.evaluate(_site(days=90), []) == []


def test_skips_when_min_days_is_none():
    s = {"key": "a.com", "cf": {"cert_expiry": {"min_days_until_expiry": None}}}
    assert cert_expiry.evaluate(s, []) == []


def test_skips_when_cert_expiry_block_missing():
    assert cert_expiry.evaluate({"key": "a.com", "cf": {}}, []) == []
    assert cert_expiry.evaluate({"key": "a.com"}, []) == []


def test_summary_includes_issuer_and_expiry_date():
    alerts = cert_expiry.evaluate(_site(days=20, expires_on="2026-06-23",
                                         issuer="DigiCert"), [])
    assert "2026-06-23" in alerts[0].summary
    assert "DigiCert" in alerts[0].summary


def test_detail_carries_full_context_for_runbook():
    alert = cert_expiry.evaluate(_site(days=10, expires_on="2026-06-13",
                                        issuer="LetsEncrypt"), [])[0]
    assert alert.detail["days_until_expiry"] == 10
    assert alert.detail["expires_on"] == "2026-06-13"
    assert alert.detail["issuer"] == "LetsEncrypt"
    assert alert.detail["pack_id"] == "pack-1"


def test_registered_in_rules_registry():
    from projects.fleet_monitoring import rules
    assert cert_expiry in rules.REGISTRY
