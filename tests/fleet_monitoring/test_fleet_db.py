from projects.fleet_monitoring.fleet_db import sync, query


def _daily(date, install, total_bytes, visits, file_bytes, db_bytes):
    return {"date": date, "install": install,
            "network_total_bytes": total_bytes, "billable_visits": visits,
            "storage_file_bytes": file_bytes, "storage_database_bytes": db_bytes}


def _snapshot(date):
    return {"date": date, "sites": [
        {"key": "a.com", "wpe": {"install": "instA"},
         "cf": {"analytics": {"cache_hit_rate": 60.0}}},
        {"key": "cfonly.com", "wpe": None,
         "cf": {"analytics": {"cache_hit_rate": 80.0}}},
    ]}


def test_sync_builds_metrics_from_daily_rows(tmp_path):
    db = tmp_path / "fleet.db"
    daily = [_daily("2026-05-18", "instA", 2_000_000_000, 100, 4_000_000_000, 1_000_000_000)]
    sync(db, snapshots=[_snapshot("2026-05-18")], daily_rows=daily, interventions=[])
    rows = query(db, "SELECT * FROM metrics")
    assert len(rows) == 1
    r = rows[0]
    assert r["site_key"] == "a.com"
    assert r["bandwidth_gb"] == 2.0
    assert r["mb_per_visit"] == 20.0
    assert r["storage_gb"] == 5.0
    assert r["billable_visits"] == 100
    assert r["cache_hit_rate"] == 60.0


def test_sync_skips_daily_rows_with_no_site_key_mapping(tmp_path):
    db = tmp_path / "fleet.db"
    daily = [_daily("2026-05-18", "unknownInstall", 1_000_000_000, 10, 0, 0)]
    sync(db, snapshots=[_snapshot("2026-05-18")], daily_rows=daily, interventions=[])
    assert query(db, "SELECT * FROM metrics") == []


def test_sync_mb_per_visit_zero_when_no_visits(tmp_path):
    db = tmp_path / "fleet.db"
    daily = [_daily("2026-05-18", "instA", 1_000_000_000, 0, 0, 0)]
    sync(db, snapshots=[_snapshot("2026-05-18")], daily_rows=daily, interventions=[])
    assert query(db, "SELECT mb_per_visit FROM metrics")[0]["mb_per_visit"] == 0.0


def test_sync_loads_only_confirmed_interventions(tmp_path):
    db = tmp_path / "fleet.db"
    interventions = [
        {"site": "a.com", "applied_date": "2026-05-13", "type": "cf_waf_rule",
         "target_metric": "bandwidth", "description": "x", "status": "confirmed",
         "fingerprint": "a.com:2026-05-13:aaa"},
        {"site": "b.com", "applied_date": "2026-05-14", "type": "cf_ssl",
         "target_metric": "bandwidth", "description": "y", "status": "needs_review",
         "fingerprint": "b.com:2026-05-14:bbb"},
        {"site": "c.com", "applied_date": "2026-05-15", "type": "cf_bot",
         "target_metric": "bandwidth", "description": "z", "status": "dismissed",
         "fingerprint": "c.com:2026-05-15:ccc"},
    ]
    sync(db, snapshots=[_snapshot("2026-05-18")], daily_rows=[],
         interventions=interventions)
    rows = query(db, "SELECT site_key, fingerprint FROM interventions")
    assert len(rows) == 1
    assert rows[0]["site_key"] == "a.com"


def test_sync_is_idempotent_rebuild(tmp_path):
    db = tmp_path / "fleet.db"
    daily = [_daily("2026-05-18", "instA", 1_000_000_000, 10, 0, 0)]
    sync(db, snapshots=[_snapshot("2026-05-18")], daily_rows=daily, interventions=[])
    sync(db, snapshots=[_snapshot("2026-05-18")], daily_rows=daily, interventions=[])
    assert len(query(db, "SELECT * FROM metrics")) == 1
