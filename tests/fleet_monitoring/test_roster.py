from projects.fleet_monitoring.roster import join_roster, roster_diff


def test_join_roster_classifies_join_state():
    installs = [
        {"name": "example-clinicprd", "id": "i1", "account_id": "a1", "primary_domain": "www.example-clinic.com"},
        {"name": "gesonly", "id": "i2", "account_id": "a1", "primary_domain": "www.gesonly.com"},
    ]
    zones = [
        {"id": "z1", "name": "example-clinic.com"},
        {"id": "z2", "name": "cfonly.com"},
    ]
    roster = join_roster(installs, zones)
    by_key = {s["key"]: s for s in roster}
    assert by_key["example-clinic.com"]["join_state"] == "wpe+cf"
    assert by_key["gesonly.com"]["join_state"] == "wpe-only"
    assert by_key["cfonly.com"]["join_state"] == "cf-only"


def test_roster_diff_reports_added_and_removed():
    old = [{"key": "a.com"}, {"key": "b.com"}]
    new = [{"key": "b.com"}, {"key": "c.com"}]
    d = roster_diff(old, new)
    assert d["added"] == ["c.com"]
    assert d["removed"] == ["a.com"]
