from projects.fleet_monitoring.models import Alert, SEVERITY_INFO
from projects.fleet_monitoring.mutes import apply_mutes


def test_apply_mutes_matches_prefix_and_respects_expiry():
    mute_entries = [
        {"fingerprint": "example-intl-client.com:config_drift", "reason": "intentional",
         "muted_by": "roland", "expires": None},
        {"fingerprint": "example-clinic.com:bot_ratio", "reason": "temp",
         "muted_by": "roland", "expires": "2020-01-01"},  # already expired
    ]
    a_muted = Alert("example-intl-client.com", "config_drift", SEVERITY_INFO, "x", {"k": 1})
    a_expired = Alert("example-clinic.com", "bot_ratio", SEVERITY_INFO, "x", {})
    a_other = Alert("example-clinic.com", "bandwidth_spike", SEVERITY_INFO, "x", {})

    result = apply_mutes([a_muted, a_expired, a_other], mute_entries, today="2026-05-16")

    assert result[0].state == "muted" and result[0].mute_reason == "intentional"
    assert result[1].state == "new"     # expired mute does not apply
    assert result[2].state == "new"     # no matching entry
