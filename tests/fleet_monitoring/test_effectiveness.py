from projects.fleet_monitoring.effectiveness import compute_one, VALID_TARGETS


def _flat_series(start_iso, days, value):
    """{date: value} for `days` consecutive days from start_iso."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(start_iso)
    return {(d0 + timedelta(days=i)).isoformat(): value for i in range(days)}


def test_compute_one_worked_when_metric_drops():
    series = {}
    series.update(_flat_series("2026-03-01", 30, 100.0))
    series.update(_flat_series("2026-03-31", 60, 60.0))
    out = compute_one(series, applied_date="2026-03-30",
                      target_metric="bandwidth", today="2026-07-01")
    by_h = {r["horizon_days"]: r for r in out}
    assert by_h[7]["verdict"] == "worked"
    assert by_h[7]["delta_pct"] == -40.0
    assert by_h[30]["verdict"] == "worked"


def test_compute_one_regressed_when_metric_rises():
    series = {}
    series.update(_flat_series("2026-03-01", 30, 100.0))
    series.update(_flat_series("2026-03-31", 60, 150.0))
    out = compute_one(series, applied_date="2026-03-30",
                      target_metric="bandwidth", today="2026-07-01")
    assert {r["horizon_days"]: r["verdict"] for r in out}[7] == "regressed"


def test_compute_one_no_effect_within_threshold():
    series = {}
    series.update(_flat_series("2026-03-01", 30, 100.0))
    series.update(_flat_series("2026-03-31", 60, 103.0))
    out = compute_one(series, applied_date="2026-03-30",
                      target_metric="bandwidth", today="2026-07-01")
    assert {r["horizon_days"]: r["verdict"] for r in out}[7] == "no_effect"


def test_compute_one_too_early_when_today_before_horizon():
    series = {}
    series.update(_flat_series("2026-03-16", 14, 100.0))
    series.update(_flat_series("2026-03-31", 5, 60.0))
    out = compute_one(series, applied_date="2026-03-30",
                      target_metric="bandwidth", today="2026-04-04")
    by_h = {r["horizon_days"]: r for r in out}
    assert by_h[7]["verdict"] == "too_early"
    assert by_h[30]["verdict"] == "too_early"
    assert by_h[90]["verdict"] == "too_early"


def test_compute_one_baseline_unavailable_with_too_few_before_days():
    series = {"2026-03-28": 100.0, "2026-03-29": 100.0}
    series.update(_flat_series("2026-03-31", 60, 50.0))
    out = compute_one(series, applied_date="2026-03-30",
                      target_metric="bandwidth", today="2026-07-01")
    assert all(r["verdict"] == "baseline_unavailable" for r in out)


def test_compute_one_too_early_when_after_window_too_sparse():
    series = {}
    series.update(_flat_series("2026-03-16", 14, 100.0))
    series["2026-03-31"] = 50.0
    series["2026-04-01"] = 50.0
    series["2026-04-02"] = 50.0
    out = compute_one(series, applied_date="2026-03-30",
                      target_metric="bandwidth", today="2026-07-01")
    assert {r["horizon_days"]: r["verdict"] for r in out}[7] == "too_early"


def test_valid_targets_excludes_cache_hit_rate():
    assert "cache_hit_rate" not in VALID_TARGETS
    assert VALID_TARGETS == {"bandwidth", "mb_per_visit", "storage"}


from projects.fleet_monitoring.effectiveness import compute
from projects.fleet_monitoring.fleet_db import sync, query


def test_compute_writes_effectiveness_rows(tmp_path):
    db = tmp_path / "fleet.db"
    interventions = [{
        "site": "a.com", "applied_date": "2026-03-30", "type": "cf_waf_rule",
        "target_metric": "bandwidth", "description": "x", "status": "confirmed",
        "fingerprint": "a.com:2026-03-30:aaa"}]
    from datetime import date, timedelta
    daily = []
    d0 = date(2026, 3, 16)
    for i in range(90):
        day = (d0 + timedelta(days=i)).isoformat()
        gb = 100.0 if day <= "2026-03-29" else 60.0
        daily.append({"date": day, "install": "instA",
                      "network_total_bytes": int(gb * 1e9),
                      "billable_visits": 10,
                      "storage_file_bytes": 0, "storage_database_bytes": 0})
    snap = {"date": "2026-06-30", "sites": [
        {"key": "a.com", "wpe": {"install": "instA"}, "cf": {"analytics": {}}}]}
    sync(db, snapshots=[snap], daily_rows=daily, interventions=interventions)

    compute(db, today="2026-07-01")
    rows = query(db, "SELECT horizon_days, verdict, delta_pct FROM effectiveness "
                     "ORDER BY horizon_days")
    assert len(rows) == 3
    by_h = {r["horizon_days"]: r for r in rows}
    assert by_h[7]["verdict"] == "worked"
    assert by_h[7]["delta_pct"] == -40.0


def test_compute_skips_unsupported_target_metric(tmp_path):
    db = tmp_path / "fleet.db"
    interventions = [{
        "site": "a.com", "applied_date": "2026-03-30", "type": "cf_cache_rule",
        "target_metric": "cache_hit_rate", "description": "x",
        "status": "confirmed", "fingerprint": "a.com:2026-03-30:bbb"}]
    snap = {"date": "2026-06-30", "sites": [
        {"key": "a.com", "wpe": {"install": "instA"}, "cf": {"analytics": {}}}]}
    sync(db, snapshots=[snap], daily_rows=[], interventions=interventions)
    compute(db, today="2026-07-01")
    assert query(db, "SELECT * FROM effectiveness") == []
