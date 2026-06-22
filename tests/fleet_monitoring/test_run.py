from projects.fleet_monitoring.run import headline, run_log_entry


def test_headline_summarizes_new_alerts():
    snapshot = {"date": "2026-05-16", "roster_summary": {"total": 247}}
    alerts = [
        {"state": "new", "severity": "critical", "site_key": "example-doc-a.com",
         "rule": "config_drift", "summary": "ssl downgraded"},
        {"state": "new", "severity": "warning", "site_key": "x.com",
         "rule": "bot_ratio", "summary": "bots"},
        {"state": "ongoing", "severity": "warning", "site_key": "y.com",
         "rule": "bot_ratio", "summary": "bots"},
    ]
    line = headline(snapshot, alerts)
    assert "247 sites" in line
    assert "2 NEW" in line
    assert "example-doc-a.com" in line          # the critical is named


def test_headline_all_clear():
    line = headline({"date": "2026-05-16", "roster_summary": {"total": 247}}, [])
    assert "0 NEW" in line or "all clear" in line.lower()


def test_run_log_entry_has_required_fields():
    entry = run_log_entry("2026-05-16", duration_s=840, coverage={"wpe": "243/248"},
                          alert_counts={"new": 2, "ongoing": 5})
    assert entry["date"] == "2026-05-16"
    assert entry["duration_s"] == 840
    assert entry["alert_counts"]["new"] == 2
    assert "logged_at" in entry


def test_sync_fleet_db_builds_db(tmp_path, monkeypatch):
    """run.sync_fleet_db rebuilds fleet.db with the 3 tables."""
    import projects.fleet_monitoring.run as rmod
    import projects.fleet_monitoring.fleet_db as fdb

    db = tmp_path / "fleet.db"
    snap = {"date": "2026-05-20", "sites": [
        {"key": "a.com", "wpe": {"install": "instA"},
         "cf": {"analytics": {"cache_hit_rate": 50.0}}}]}
    daily = [{"date": "2026-05-20", "install": "instA",
              "network_total_bytes": 1_000_000_000, "billable_visits": 10,
              "storage_file_bytes": 0, "storage_database_bytes": 0}]
    rmod.sync_fleet_db(db_path=db, snapshots=[snap], daily_rows=daily,
                       interventions=[], today="2026-05-21")
    tables = {r["name"] for r in fdb.query(
        db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"metrics", "interventions", "effectiveness"} <= tables


def test_pull_analytics_lake_runs_three_skill_subprocesses(monkeypatch):
    """Refresh + cron pipeline pulls fresh GA4/GSC into the lake before collect."""
    from projects.fleet_monitoring import run as run_mod
    calls = []

    class _R:
        pass

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _R()

    monkeypatch.setattr(run_mod.subprocess, "run", fake_run)
    run_mod._pull_analytics_lake()
    joined = [" ".join(c) for c in calls]
    assert any("skills.analytics.discover" in m for m in joined)
    assert any("skills.analytics.gsc_pull" in m for m in joined)
    assert any("skills.analytics.ga4_pull" in m for m in joined)


def test_pull_analytics_lake_isolates_per_step_failures(monkeypatch, capsys):
    """One pull failing must not abort the others — degrade to a stderr line."""
    import subprocess as _sub
    from projects.fleet_monitoring import run as run_mod
    calls = []

    class _R:
        pass

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "gsc_pull" in " ".join(cmd):
            raise _sub.CalledProcessError(1, cmd)
        return _R()

    monkeypatch.setattr(run_mod.subprocess, "run", fake_run)
    run_mod._pull_analytics_lake()        # must not raise
    assert len(calls) == 3
    err = capsys.readouterr().err
    assert "gsc_pull" in err and "skipped" in err
