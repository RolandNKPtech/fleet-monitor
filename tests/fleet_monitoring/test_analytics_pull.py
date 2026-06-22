"""Tests for analytics aggregation off the local lake."""
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from projects.fleet_monitoring import analytics_pull


def _write_ga4(tmp_path: Path, rows: list[dict]) -> None:
    out = tmp_path / "ga4" / "property_metrics"
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), out / "all.parquet")


def _write_gsc(tmp_path: Path, host: str, rows: list[dict]) -> None:
    out = tmp_path / "gsc" / "search_analytics" / host
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), out / "2026-05.parquet")


def _mapping(apex, prop=None, gsc=None):
    return {apex: {"ga4_property_id": prop, "ga4_source": "auto" if prop else None,
                   "gsc_site_url": gsc, "gsc_source": "auto" if gsc else None}}


def test_ga4_30d_aggregates_sessions_and_conversions(tmp_path):
    today = date(2026, 5, 29)
    rows = []
    for i in range(30):
        rows.append({"date": today - timedelta(days=i), "property_id": "111",
                     "sessions": 10, "conversions": 1, "engagement_rate": 0.5,
                     "active_users": 8, "total_users": 9,
                     "screen_page_views": 30, "avg_session_duration": 100.0,
                     "account": "x", "pulled_at": None})
    _write_ga4(tmp_path, rows)
    m = _mapping("a.com", prop="111")
    out = analytics_pull.pull(m, today=today, lake_path=tmp_path)
    g = out["a.com"]["ga4"]
    assert g["sessions_30d"] == 300
    assert g["conversions_30d"] == 30


def test_ga4_engagement_rate_is_sessions_weighted(tmp_path):
    today = date(2026, 5, 29)
    rows = [
        {"date": today - timedelta(days=1), "property_id": "111",
         "sessions": 100, "conversions": 0, "engagement_rate": 0.6,
         "active_users": 0, "total_users": 0, "screen_page_views": 0,
         "avg_session_duration": 0.0, "account": "x", "pulled_at": None},
        {"date": today - timedelta(days=2), "property_id": "111",
         "sessions": 900, "conversions": 0, "engagement_rate": 0.5,
         "active_users": 0, "total_users": 0, "screen_page_views": 0,
         "avg_session_duration": 0.0, "account": "x", "pulled_at": None},
    ]
    _write_ga4(tmp_path, rows)
    out = analytics_pull.pull(_mapping("a.com", prop="111"),
                              today=today, lake_path=tmp_path)
    # (100*0.6 + 900*0.5) / 1000 = 0.51
    assert abs(out["a.com"]["ga4"]["engagement_rate"] - 0.51) < 1e-9


def test_ga4_wow_windows_7d_and_prev_7d(tmp_path):
    today = date(2026, 5, 29)
    rows = []
    # Days -7..-1 (current 7d): 10 sessions each = 70
    for i in range(1, 8):
        rows.append({"date": today - timedelta(days=i), "property_id": "111",
                     "sessions": 10, "conversions": 1, "engagement_rate": 0.5,
                     "active_users": 0, "total_users": 0, "screen_page_views": 0,
                     "avg_session_duration": 0.0, "account": "x", "pulled_at": None})
    # Days -14..-8 (prior 7d): 20 sessions each = 140
    for i in range(8, 15):
        rows.append({"date": today - timedelta(days=i), "property_id": "111",
                     "sessions": 20, "conversions": 2, "engagement_rate": 0.5,
                     "active_users": 0, "total_users": 0, "screen_page_views": 0,
                     "avg_session_duration": 0.0, "account": "x", "pulled_at": None})
    _write_ga4(tmp_path, rows)
    out = analytics_pull.pull(_mapping("a.com", prop="111"),
                              today=today, lake_path=tmp_path)
    g = out["a.com"]["ga4"]
    assert g["sessions_7d"] == 70
    assert g["sessions_prev_7d"] == 140
    assert g["conversions_7d"] == 7
    assert g["conversions_prev_7d"] == 14


def test_gsc_30d_and_wow(tmp_path):
    today = date(2026, 5, 29)
    rows = []
    for i in range(30):
        rows.append({"date": today - timedelta(days=i), "host": "a.com",
                     "query": "q", "page": "/", "country": "us", "device": "desktop",
                     "clicks": 5, "impressions": 100, "position": 3.0})
    _write_gsc(tmp_path, "a.com", rows)
    out = analytics_pull.pull(_mapping("a.com", gsc="sc-domain:a.com"),
                              today=today, lake_path=tmp_path)
    s = out["a.com"]["gsc"]
    assert s["clicks_30d"] == 150
    assert s["impressions_30d"] == 3000
    assert s["clicks_7d"] == 35
    assert s["clicks_prev_7d"] == 35


def test_missing_coverage_yields_none_blocks(tmp_path):
    today = date(2026, 5, 29)
    # No parquet files written at all.
    (tmp_path / "ga4" / "property_metrics").mkdir(parents=True)
    (tmp_path / "gsc" / "search_analytics").mkdir(parents=True)
    out = analytics_pull.pull(
        {"a.com": {"ga4_property_id": "111", "ga4_source": "auto",
                   "gsc_site_url": "sc-domain:a.com", "gsc_source": "auto"}},
        today=today, lake_path=tmp_path)
    assert out["a.com"]["ga4"] is None
    assert out["a.com"]["gsc"] is None


def test_apex_with_no_mapping_is_skipped(tmp_path):
    today = date(2026, 5, 29)
    (tmp_path / "ga4" / "property_metrics").mkdir(parents=True)
    (tmp_path / "gsc" / "search_analytics").mkdir(parents=True)
    out = analytics_pull.pull(
        {"a.com": {"ga4_property_id": None, "ga4_source": None,
                   "gsc_site_url": None, "gsc_source": None}},
        today=today, lake_path=tmp_path)
    assert out["a.com"] == {"ga4": None, "gsc": None}
