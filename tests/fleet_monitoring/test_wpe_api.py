from projects.fleet_monitoring.wpe_api import parse_usage_rollup


def test_parse_usage_rollup_extracts_bandwidth_split_and_visits():
    raw = {
        "metrics_rollup": {
            "network_total_bytes": {"sum": 113822289247},
            "network_origin_bytes": {"sum": 3833459439},
            "network_cdn_bytes": {"sum": 109988829808},
            "billable_visits": {"sum": 3313},
            "visit_count": {"sum": 26481},
            "storage_file_bytes": {"latest": {"value": 1000000000}},
            "storage_database_bytes": {"latest": {"value": 716338688}},
        }
    }
    m = parse_usage_rollup(raw)
    assert m["bandwidth_gb_30d"] == 113.82
    assert m["origin_gb_30d"] == 3.83
    assert m["cdn_gb_30d"] == 109.99
    assert m["billable_visits_30d"] == 3313
    assert m["total_visits_30d"] == 26481
    assert m["storage_gb"] == 1.72
    assert m["mb_per_visit"] == 34.4


def test_parse_usage_rollup_handles_missing_metrics():
    assert parse_usage_rollup({}) is None
    assert parse_usage_rollup({"metrics_rollup": {}}) is not None  # zeros, not None


from projects.fleet_monitoring.wpe_api import parse_usage_daily


def test_parse_usage_daily_extracts_normalized_daily_rows():
    raw = {
        "environment_name": "production",
        "metrics_rollup": {},
        "metrics": [
            {"date": "2026-05-13", "network_total_bytes": "1000000000",
             "network_origin_bytes": "200000000", "network_cdn_bytes": "800000000",
             "billable_visits": "120", "visit_count": "1500",
             "storage_file_bytes": "5000000000",
             "storage_database_bytes": "100000000"},
            {"date": "2026-05-14", "network_total_bytes": None,
             "billable_visits": None},
        ],
    }
    rows = parse_usage_daily(raw)
    assert len(rows) == 2

    assert rows[0] == {
        "date": "2026-05-13",
        "network_total_bytes": 1000000000,
        "network_origin_bytes": 200000000,
        "network_cdn_bytes": 800000000,
        "billable_visits": 120,
        "visit_count": 1500,
        "storage_file_bytes": 5000000000,
        "storage_database_bytes": 100000000,
    }
    # Missing/None values become 0 — predictable for downstream sums.
    assert rows[1]["network_total_bytes"] == 0
    assert rows[1]["billable_visits"] == 0


def test_parse_usage_daily_empty_when_no_metrics_key():
    assert parse_usage_daily({}) == []
    assert parse_usage_daily({"metrics_rollup": {}}) == []
    assert parse_usage_daily({"metrics": []}) == []
