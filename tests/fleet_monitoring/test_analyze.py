from projects.fleet_monitoring.analyze import build_histories, analyze_snapshot


def test_build_histories_groups_prior_entries_by_key_oldest_first():
    older = {"date": "2026-05-14", "sites": [{"key": "a.com", "wpe": {"bandwidth_gb_30d": 10}}]}
    newer = {"date": "2026-05-15", "sites": [{"key": "a.com", "wpe": {"bandwidth_gb_30d": 20}}]}
    hist = build_histories([older, newer])
    assert [h["wpe"]["bandwidth_gb_30d"] for h in hist["a.com"]] == [10, 20]


def test_analyze_snapshot_attaches_alerts_and_counts():
    history_snaps = [
        {"date": f"2026-05-{d:02d}", "sites": [{"key": "x.com",
         "wpe": {"bandwidth_gb_30d": 40}}]} for d in range(1, 9)
    ]
    current = {"date": "2026-05-09", "schema_version": 1, "sites": [
        {"key": "x.com", "join_state": "wpe-only",
         "wpe": {"bandwidth_gb_30d": 90}, "cf": None, "probe": None, "overlay": None},
    ]}
    enriched, alerts = analyze_snapshot(current, history_snaps,
                                        previous_alerts=[], mute_entries=[])
    assert enriched["sites"][0]["alerts_count"] >= 1
    assert any(a.rule == "bandwidth_spike" for a in alerts)
    assert all(a.state in ("new", "ongoing", "resolved", "muted") for a in alerts)


def test_analyze_snapshot_emits_plan_utilization_alerts(tmp_path, monkeypatch):
    """When wpe-plans.yml + daily.jsonl are populated, plan_utilization fires."""
    from projects.fleet_monitoring.analyze import analyze_snapshot
    import projects.fleet_monitoring.plan_config as pc
    import projects.fleet_monitoring.timeseries as ts

    plans = tmp_path / "wpe-plans.yml"
    plans.write_text(
        "accounts:\n"
        "  acctE: {cycle_start_day: 13, bandwidth_gb_limit: 100, "
        "visits_limit: null, overage_per_gb_usd: null}\n", encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", plans)

    daily = tmp_path / "daily.jsonl"
    import json
    daily.write_text(json.dumps({
        "date": "2026-05-13", "account": "acctE", "install": "x",
        "network_total_bytes": 96_000_000_000, "billable_visits": 0,
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(ts, "DAILY_FILE", daily)

    snapshot = {
        "date": "2026-05-19", "schema_version": 1,
        "sites": [{"key": "example-clinic.com", "join_state": "wpe-only",
                   "wpe": {"account_name": "acctE",
                           "bandwidth_gb_30d": 50},
                   "cf": None, "probe": None, "overlay": None}],
    }
    enriched, alerts = analyze_snapshot(
        snapshot, [], previous_alerts=[], mute_entries=[])
    plan_alerts = [a for a in alerts if a.rule == "plan_utilization"]
    assert len(plan_alerts) == 1
    assert plan_alerts[0].severity == "critical"
    assert plan_alerts[0].site_key == "acctE"


def test_analyze_appends_drift_drafts_to_interventions_yml(tmp_path, monkeypatch):
    """A `us`-attributed config_drift alert becomes a needs_review draft."""
    import projects.fleet_monitoring.analyze as amod
    import projects.fleet_monitoring.interventions as imod

    iv_file = tmp_path / "interventions.yml"
    monkeypatch.setattr(imod, "INTERVENTIONS_FILE", iv_file)

    snapshot = {"date": "2026-05-20", "alerts": [
        {"site_key": "a.com", "rule": "config_drift", "severity": "warning",
         "summary": "x", "detail": {"kind": "waf_rule_added", "field": "waf",
         "old": "0", "new": "1", "attribution": "us"}},
    ]}
    amod.write_drift_drafts(snapshot, iv_file)
    drafts = imod.load_interventions(iv_file)
    assert len(drafts) == 1
    assert drafts[0]["status"] == "needs_review"
    assert drafts[0]["site"] == "a.com"
