"""Tests for skills.analytics.ga4_pull pure logic (no API calls)."""

from datetime import date, datetime, timezone

import pyarrow.parquet as pq

from skills.analytics import ga4_pull


def test_parse_property_metrics_valid():
    api_rows = [{
        "date": "20260525",
        "sessions": "31", "activeUsers": "29", "totalUsers": "30",
        "screenPageViews": "114", "conversions": "2",
        "engagementRate": "0.903", "averageSessionDuration": "85.3",
    }]
    rows = ga4_pull.parse_property_metrics(api_rows, "319608385", "analyticsuser2",
                                            datetime(2026, 5, 27, tzinfo=timezone.utc))
    assert len(rows) == 1
    r = rows[0]
    assert r["date"] == date(2026, 5, 25)
    assert r["property_id"] == "319608385"
    assert r["sessions"] == 31
    assert r["total_users"] == 30
    assert r["engagement_rate"] == 0.903
    assert r["account"] == "analyticsuser2"


def test_parse_property_metrics_handles_missing_fields():
    api_rows = [{"date": "20260525"}]  # no metrics at all
    rows = ga4_pull.parse_property_metrics(api_rows, "p", "a", datetime.now(timezone.utc))
    assert len(rows) == 1
    assert rows[0]["sessions"] == 0
    assert rows[0]["engagement_rate"] == 0.0


def test_parse_property_metrics_skips_bad_date():
    api_rows = [{"date": "BAD", "sessions": "5"}]
    rows = ga4_pull.parse_property_metrics(api_rows, "p", "a", datetime.now(timezone.utc))
    assert rows == []


def test_parse_traffic_sources_valid():
    api_rows = [{
        "date": "20260525",
        "sessionSource": "google", "sessionMedium": "organic",
        "sessionDefaultChannelGroup": "Organic Search",
        "sessions": "8", "activeUsers": "7", "conversions": "0",
    }]
    rows = ga4_pull.parse_traffic_sources(api_rows, "p1", "acc", datetime.now(timezone.utc))
    assert len(rows) == 1
    assert rows[0]["session_source"] == "google"
    assert rows[0]["session_medium"] == "organic"
    assert rows[0]["sessions"] == 8


def test_parse_top_pages_valid():
    api_rows = [{
        "date": "20260525",
        "pagePath": "/about",
        "landingPage": "/",
        "screenPageViews": "12",
        "activeUsers": "10",
        "averageSessionDuration": "45.5",
    }]
    rows = ga4_pull.parse_top_pages(api_rows, "p1", "acc", datetime.now(timezone.utc))
    assert len(rows) == 1
    assert rows[0]["page_path"] == "/about"
    assert rows[0]["landing_page"] == "/"
    assert rows[0]["screen_page_views"] == 12


def test_write_partitions_routes_by_report(tmp_path, monkeypatch):
    monkeypatch.setattr(ga4_pull, "GA4_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    row = {
        "date": date(2026, 5, 25),
        "property_id": "p1",
        "sessions": 1, "active_users": 1, "total_users": 1,
        "screen_page_views": 1, "conversions": 0,
        "engagement_rate": 0.5, "avg_session_duration": 30.0,
        "account": "acc", "pulled_at": now,
    }
    written = ga4_pull.write_partitions([row], "property_metrics", "p1")
    assert written == {"2026-05": 1}
    assert (tmp_path / "property_metrics" / "p1" / "2026-05.parquet").exists()


def test_write_partitions_idempotent_for_same_date(tmp_path, monkeypatch):
    monkeypatch.setattr(ga4_pull, "GA4_DIR", tmp_path)
    base = {
        "date": date(2026, 5, 25), "property_id": "p1",
        "sessions": 5, "active_users": 5, "total_users": 5,
        "screen_page_views": 10, "conversions": 0,
        "engagement_rate": 0.5, "avg_session_duration": 30.0,
        "account": "a", "pulled_at": datetime.now(timezone.utc),
    }
    ga4_pull.write_partitions([base], "property_metrics", "p1")
    ga4_pull.write_partitions([{**base, "sessions": 99}], "property_metrics", "p1")
    tbl = pq.read_table(tmp_path / "property_metrics" / "p1" / "2026-05.parquet")
    assert tbl.num_rows == 1
    assert tbl.column("sessions").to_pylist() == [99]
