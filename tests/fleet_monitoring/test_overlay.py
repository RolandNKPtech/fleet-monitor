from projects.fleet_monitoring.overlay import build_overlay_index


def test_build_overlay_index_keys_by_apex():
    tracker = {"sites": [
        {"install": "example-clinicprd", "apex": "example-clinic.com", "fix_date": "2026-05-01",
         "pre_fix_bandwidth_gb_30d": 167.6, "pre_fix_mb_per_visit": 37.6,
         "skip_country_challenge": False},
    ]}
    idx = build_overlay_index(tracker)
    assert "example-clinic.com" in idx
    assert idx["example-clinic.com"]["fixed"] is True
    assert idx["example-clinic.com"]["fix_date"] == "2026-05-01"
    assert idx["example-clinic.com"]["pre_fix_bandwidth_gb_30d"] == 167.6
