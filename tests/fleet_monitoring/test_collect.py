from projects.fleet_monitoring.collect import assemble_snapshot


def test_assemble_snapshot_merges_sources_and_records_coverage():
    roster = [
        {"key": "example-clinic.com", "apex": "example-clinic.com", "join_state": "wpe+cf",
         "wpe_install": "example-clinicprd", "wpe_account": "a1", "wpe_install_id": "i1",
         "cf_zone_id": "z1"},
        {"key": "cfonly.com", "apex": "cfonly.com", "join_state": "cf-only",
         "wpe_install": None, "wpe_account": None, "wpe_install_id": None,
         "cf_zone_id": "z2"},
    ]
    wpe_metrics = {"i1": {"bandwidth_gb_30d": 113.8, "billable_visits_30d": 3313,
                          "mb_per_visit": 34.4}}
    cf_configs = {"z1": {"settings": {"ssl": "strict"}}, "z2": {"error": "boom"}}
    cf_analytics = {"z1": {"requests_30d": 100, "threats": 5, "cache_hit_rate": 60.0},
                    "z2": {"requests_30d": 0, "threats": 0, "cache_hit_rate": 0.0}}
    overlay_idx = {"example-clinic.com": {"fixed": True, "fix_date": "2026-05-01"}}
    probes = {"example-clinic.com": {"GPTBot": {"http": 200, "expected": 200, "ok": True}}}

    snap = assemble_snapshot("2026-05-16", roster, wpe_metrics, cf_configs,
                             cf_analytics, overlay_idx, probes)

    assert snap["schema_version"] == 1
    assert snap["date"] == "2026-05-16"
    by_key = {s["key"]: s for s in snap["sites"]}
    assert by_key["example-clinic.com"]["wpe"]["bandwidth_gb_30d"] == 113.8
    assert by_key["example-clinic.com"]["overlay"]["fixed"] is True
    assert by_key["example-clinic.com"]["probe"]["GPTBot"]["ok"] is True
    assert by_key["cfonly.com"]["wpe"] is None
    # coverage: example-clinic cf config ok, cfonly cf config errored
    assert snap["run"]["coverage"]["cf_config"] == "1/2"
    assert snap["run"]["coverage"]["wpe"] == "1/1"   # only 1 site has a wpe install


def test_assemble_snapshot_keeps_daily_array_when_provided():
    """`collect()` attaches per-install daily metrics under wpe.daily."""
    from projects.fleet_monitoring.collect import assemble_snapshot
    roster = [{"key": "example-clinic.com", "apex": "example-clinic.com", "join_state": "wpe+cf",
               "wpe_install": "example-clinicprd", "wpe_account": "uuid-5",
               "wpe_install_id": "i1", "cf_zone_id": "z1"}]
    wpe_metrics = {"i1": {"bandwidth_gb_30d": 100.0, "billable_visits_30d": 1000,
                          "mb_per_visit": 30.0}}
    wpe_daily = {"i1": [
        {"date": "2026-05-19", "network_total_bytes": 1_000_000_000,
         "billable_visits": 200, "visit_count": 0,
         "network_origin_bytes": 0, "network_cdn_bytes": 0,
         "storage_file_bytes": 0, "storage_database_bytes": 0}]}
    accounts = {"uuid-5": "acctE"}
    snap = assemble_snapshot(
        "2026-05-19", roster, wpe_metrics, {}, {}, {}, {}, 0,
        accounts=accounts, wpe_daily=wpe_daily)
    daily = snap["sites"][0]["wpe"]["daily"]
    assert len(daily) == 1
    assert daily[0]["date"] == "2026-05-19"
    assert daily[0]["network_total_bytes"] == 1_000_000_000


def test_assemble_snapshot_attaches_per_site_block_when_provided():
    from projects.fleet_monitoring.collect import assemble_snapshot
    roster = [{"key": "a.com", "apex": "a.com", "join_state": "wpe+cf",
               "wpe_install_id": "i1", "wpe_install": "i1",
               "wpe_account": "u1", "cf_zone_id": "z1"}]
    per_site = {"z1": {"fetched_at": "2026-05-19T22:00Z",
                       "country_window_days": 30, "traffic_window_days": 7,
                       "total_requests_7d": 700,
                       "countries": [{"country": "US", "requests": 100, "bytes": 500}],
                       "requests_threats_daily": [], "top_paths": [], "top_uas": []}}
    snap = assemble_snapshot(
        today="2026-05-19", roster=roster,
        wpe_metrics={"i1": {"bandwidth_gb_30d": 10}},
        cf_configs={"z1": {"settings": {}}},
        cf_analytics={"z1": {"requests_30d": 100, "threats": 0, "cache_hit_rate": 50}},
        overlay_idx={}, probes={}, per_site=per_site,
        accounts={"u1": "acctA"}, wpe_daily={})
    site = snap["sites"][0]
    assert site["cf"]["per_site"]["countries"][0]["country"] == "US"
    assert site["cf"]["per_site"]["traffic_window_days"] == 7


def test_assemble_snapshot_attaches_analytics_block_per_site():
    """When `analytics` is passed, each site's entry["analytics"] mirrors it."""
    from projects.fleet_monitoring.collect import assemble_snapshot
    roster = [{"key": "a.com", "apex": "a.com", "join_state": "wpe+cf",
               "wpe_install_id": "i1", "wpe_install": "i1name",
               "wpe_account": "u1", "cf_zone_id": "z1"}]
    snap = assemble_snapshot(
        today="2026-05-29", roster=roster,
        wpe_metrics={"i1": {"bandwidth_gb_30d": 10.0}},
        cf_configs={"z1": {"settings": {}}},
        cf_analytics={"z1": {"requests_30d": 1000}},
        overlay_idx={}, probes={},
        analytics={"a.com": {"ga4": {"property_id": "111",
                                     "sessions_30d": 100},
                             "gsc": None}})
    site = snap["sites"][0]
    assert site["analytics"]["ga4"]["sessions_30d"] == 100
    assert site["analytics"]["gsc"] is None


def test_assemble_snapshot_analytics_block_is_none_when_unmapped():
    from projects.fleet_monitoring.collect import assemble_snapshot
    roster = [{"key": "b.com", "apex": "b.com", "join_state": "cf-only",
               "cf_zone_id": "z2"}]
    snap = assemble_snapshot(
        today="2026-05-29", roster=roster,
        wpe_metrics={}, cf_configs={"z2": {}}, cf_analytics={},
        overlay_idx={}, probes={})
    site = snap["sites"][0]
    assert site["analytics"] == {"ga4": None, "gsc": None}
