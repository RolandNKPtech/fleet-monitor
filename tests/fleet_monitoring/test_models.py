from projects.fleet_monitoring.models import Alert, SEVERITY_CRITICAL, SEVERITY_ORDER


def test_alert_fingerprint_is_stable_across_drifting_detail():
    """Same site+rule must produce the same fingerprint regardless of
    drifting metric values. Otherwise the lifecycle treats every daily
    metric change as a brand-new alert, and resolved copies pile up forever
    (the bug that put 37 alert rows on one site)."""
    a1 = Alert(site_key="example-clinic.com", rule="mb_per_visit_high",
               severity=SEVERITY_CRITICAL, summary="x", detail={"mb": 43})
    a2 = Alert(site_key="example-clinic.com", rule="mb_per_visit_high",
               severity=SEVERITY_CRITICAL, summary="x", detail={"mb": 44})
    assert a1.fingerprint() == a2.fingerprint()
    assert a1.fingerprint() == "example-clinic.com:mb_per_visit_high"
    assert a1.state == "new"
    assert SEVERITY_ORDER[SEVERITY_CRITICAL] == 0


def test_alert_fingerprint_uses_dedup_key_for_multi_per_site_rules():
    """Rules that legitimately emit several alerts per site (e.g.
    plan_utilization with one alert per axis) opt in via dedup_key so the
    lifecycle can track them separately."""
    bw = Alert(site_key="acct-a", rule="plan_utilization",
               severity=SEVERITY_CRITICAL, summary="x",
               detail={"axis": "bandwidth"}, dedup_key="bandwidth")
    vi = Alert(site_key="acct-a", rule="plan_utilization",
               severity=SEVERITY_CRITICAL, summary="x",
               detail={"axis": "visits"}, dedup_key="visits")
    assert bw.fingerprint() == "acct-a:plan_utilization:bandwidth"
    assert vi.fingerprint() == "acct-a:plan_utilization:visits"
    assert bw.fingerprint() != vi.fingerprint()


def test_alert_dict_round_trip_preserves_dedup_key():
    a = Alert(site_key="x.com", rule="config_drift",
              severity=SEVERITY_CRITICAL, summary="x",
              detail={"field": "ssl"}, dedup_key="ssl")
    b = Alert.from_dict(a.to_dict())
    assert b.dedup_key == "ssl"
    assert b.fingerprint() == a.fingerprint()
