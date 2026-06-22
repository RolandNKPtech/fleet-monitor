import json
from projects.fleet_monitoring.timeseries import rollup_rows, append_rollup, read_series


def test_rollup_rows_extracts_one_row_per_site():
    snapshot = {
        "date": "2026-05-16",
        "sites": [
            {"key": "example-clinic.com",
             "wpe": {"account_name": "acctE", "bandwidth_gb_30d": 113.8,
                     "billable_visits_30d": 3313, "mb_per_visit": 34.4},
             "cf": {"analytics": {"cache_hit_rate": 78.0, "threats": 12}},
             "alerts_count": 1},
        ],
    }
    rows = rollup_rows(snapshot)
    assert rows == [{"date": "2026-05-16", "key": "example-clinic.com",
                     "account": "acctE", "bandwidth_gb": 113.8,
                     "billable_visits": 3313, "mb_per_visit": 34.4,
                     "cache_hit_rate": 78.0, "threats": 12, "alert_count": 1,
                     "ga4_sessions": None, "ga4_conversions": None, "gsc_clicks": None}]


def test_append_and_read_roundtrip(tmp_path, monkeypatch):
    import projects.fleet_monitoring.timeseries as ts
    f = tmp_path / "timeseries.jsonl"
    monkeypatch.setattr(ts, "TIMESERIES_FILE", f)
    append_rollup([{"date": "2026-05-16", "key": "example-clinic.com", "bandwidth_gb": 113.8,
                    "billable_visits": 3313, "mb_per_visit": 34.4,
                    "cache_hit_rate": 78.0, "threats": 12, "alert_count": 1}])
    series = read_series("example-clinic.com")
    assert len(series) == 1 and series[0]["bandwidth_gb"] == 113.8


def test_append_rollup_is_idempotent_per_date(tmp_path, monkeypatch):
    """Re-running on the same date replaces that day's rows, not duplicate."""
    import projects.fleet_monitoring.timeseries as ts
    f = tmp_path / "timeseries.jsonl"
    monkeypatch.setattr(ts, "TIMESERIES_FILE", f)
    base = {"key": "example-clinic.com", "billable_visits": 0, "mb_per_visit": 0,
            "cache_hit_rate": None, "threats": None, "alert_count": 0}
    # Day 1
    append_rollup([{**base, "date": "2026-05-15", "bandwidth_gb": 100.0}])
    # Day 2 — different date, should accumulate
    append_rollup([{**base, "date": "2026-05-16", "bandwidth_gb": 110.0}])
    # Day 2 AGAIN — same date, should REPLACE the prior 2026-05-16 row
    append_rollup([{**base, "date": "2026-05-16", "bandwidth_gb": 95.0}])
    series = read_series("example-clinic.com")
    assert len(series) == 2                              # not 3
    by_date = {r["date"]: r["bandwidth_gb"] for r in series}
    assert by_date == {"2026-05-15": 100.0, "2026-05-16": 95.0}


from projects.fleet_monitoring.timeseries import (
    daily_rollup_rows, append_daily, read_daily_all)


def test_daily_rollup_rows_extracts_one_row_per_install_per_day():
    snapshot = {
        "date": "2026-05-19",
        "sites": [
            {"key": "example-clinic.com", "wpe": {
                "install": "example-clinicprd", "account_name": "acctE",
                "account": "uuid-5",
                "daily": [
                    {"date": "2026-05-18", "network_total_bytes": 1_000_000_000,
                     "network_origin_bytes": 100_000_000, "network_cdn_bytes": 900_000_000,
                     "billable_visits": 100, "visit_count": 1000,
                     "storage_file_bytes": 50_000_000, "storage_database_bytes": 1_000_000},
                    {"date": "2026-05-19", "network_total_bytes": 2_000_000_000,
                     "network_origin_bytes": 200_000_000, "network_cdn_bytes": 1_800_000_000,
                     "billable_visits": 200, "visit_count": 2000,
                     "storage_file_bytes": 55_000_000, "storage_database_bytes": 1_000_000},
                ],
            }},
            {"key": "cfonly.com", "wpe": None},          # cf-only site, skipped
        ],
    }
    rows = daily_rollup_rows(snapshot)
    assert len(rows) == 2                                # 1 install × 2 days
    assert rows[0]["install"] == "example-clinicprd"
    assert rows[0]["account"] == "acctE"
    assert rows[0]["account_id"] == "uuid-5"
    assert rows[0]["date"] == "2026-05-18"
    assert rows[0]["network_total_bytes"] == 1_000_000_000
    assert rows[1]["date"] == "2026-05-19"
    assert rows[1]["billable_visits"] == 200


def test_daily_rollup_rows_drops_sites_with_no_account_anchor():
    """Site whose wpe block has neither account_name nor account UUID is
    skipped — otherwise account-keyed aggregation groups it under None and
    plan_utilization silently shows 0% used (made-up number)."""
    snapshot = {
        "date": "2026-05-19",
        "sites": [
            {"key": "ok.com", "wpe": {
                "install": "ok", "account_name": "acctE", "account": "u5",
                "daily": [{"date": "2026-05-19", "network_total_bytes": 100,
                           "billable_visits": 1}],
            }},
            {"key": "orphan.com", "wpe": {
                "install": "orphan", "account_name": None, "account": None,
                "daily": [{"date": "2026-05-19", "network_total_bytes": 99,
                           "billable_visits": 1}],
            }},
        ],
    }
    rows = daily_rollup_rows(snapshot)
    assert len(rows) == 1
    assert rows[0]["install"] == "ok"


def test_append_daily_idempotent_per_date(tmp_path, monkeypatch):
    """Re-running on the same day replaces that day's rows, not duplicates."""
    import projects.fleet_monitoring.timeseries as ts
    f = tmp_path / "daily.jsonl"
    monkeypatch.setattr(ts, "DAILY_FILE", f)
    base = {"install": "x", "account": "a", "account_id": "u",
            "network_origin_bytes": 0, "network_cdn_bytes": 0,
            "billable_visits": 0, "visit_count": 0,
            "storage_file_bytes": 0, "storage_database_bytes": 0}
    append_daily([{**base, "date": "2026-05-18", "network_total_bytes": 1000}])
    append_daily([{**base, "date": "2026-05-19", "network_total_bytes": 2000}])
    # Same date again — replaces, not appends
    append_daily([{**base, "date": "2026-05-19", "network_total_bytes": 9999}])
    rows = read_daily_all()
    assert len(rows) == 2
    by_date = {r["date"]: r["network_total_bytes"] for r in rows}
    assert by_date == {"2026-05-18": 1000, "2026-05-19": 9999}


def test_rollup_rows_includes_analytics_fields_per_site():
    from projects.fleet_monitoring.timeseries import rollup_rows
    snapshot = {"date": "2026-05-29", "sites": [
        {"key": "a.com", "wpe": {"account_name": "x", "bandwidth_gb_30d": 10.0,
                                 "billable_visits_30d": 500, "mb_per_visit": 20.0},
         "cf": {"analytics": {"cache_hit_rate": 80.0, "threats": 0}},
         "alerts_count": 0,
         "analytics": {
             "ga4": {"sessions_7d": 70, "conversions_7d": 7},
             "gsc": {"clicks_7d": 35}}},
        {"key": "b.com", "wpe": {}, "cf": {"analytics": {}},
         "alerts_count": 0,
         "analytics": {"ga4": None, "gsc": None}},
    ]}
    rows = {r["key"]: r for r in rollup_rows(snapshot)}
    a = rows["a.com"]
    assert a["ga4_sessions"] == 70
    assert a["ga4_conversions"] == 7
    assert a["gsc_clicks"] == 35
    b = rows["b.com"]
    assert b["ga4_sessions"] is None
    assert b["ga4_conversions"] is None
    assert b["gsc_clicks"] is None
