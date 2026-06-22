"""Tests for skills.analytics.gsc_pull pure logic (no API calls)."""

from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from skills.analytics import gsc_pull
from skills.analytics.discover import _normalize_host


def test_normalize_host_strips_sc_domain():
    assert _normalize_host("sc-domain:example.com") == "example.com"


def test_normalize_host_strips_protocol():
    assert _normalize_host("https://www.example.com/") == "www.example.com"
    assert _normalize_host("http://example.com") == "example.com"


def test_normalize_host_lowercases():
    assert _normalize_host("sc-domain:EXAMPLE.COM") == "example.com"


def test_safe_host_dirname_replaces_colons():
    assert gsc_pull._safe_host_dirname("example.com") == "example.com"
    assert gsc_pull._safe_host_dirname("host:8080") == "host_8080"


def test_month_key_format():
    assert gsc_pull._month_key(date(2026, 1, 5)) == "2026-01"
    assert gsc_pull._month_key(date(2026, 12, 31)) == "2026-12"


def test_parse_rows_valid_shape():
    api_rows = [
        {
            "keys": ["2026-05-25", "med spa marketing", "https://example.com/", "usa", "DESKTOP"],
            "clicks": 5,
            "impressions": 120,
            "ctr": 0.0417,
            "position": 3.2,
        },
        {
            "keys": ["2026-05-26", "plastic surgery seo", "https://example.com/seo/", "can", "MOBILE"],
            "clicks": 0,
            "impressions": 8,
            "ctr": 0.0,
            "position": 12.5,
        },
    ]
    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    rows = gsc_pull.parse_rows(api_rows, "sc-domain:example.com", "example.com", "analyticsuser", now)
    assert len(rows) == 2
    assert rows[0]["date"] == date(2026, 5, 25)
    assert rows[0]["query"] == "med spa marketing"
    assert rows[0]["clicks"] == 5
    assert rows[0]["impressions"] == 120
    assert rows[0]["device"] == "DESKTOP"
    assert rows[0]["account"] == "analyticsuser"
    assert rows[0]["host"] == "example.com"
    assert rows[0]["site_url"] == "sc-domain:example.com"


def test_parse_rows_skips_malformed_keys():
    api_rows = [
        {"keys": ["2026-05-25", "q"], "clicks": 1, "impressions": 1},  # too few dims
        {"keys": ["BAD-DATE", "q", "p", "us", "DESKTOP"], "clicks": 1, "impressions": 1},  # bad date
    ]
    rows = gsc_pull.parse_rows(api_rows, "ex", "ex", "acc", datetime.now(timezone.utc))
    assert rows == []


def test_parse_rows_handles_missing_metrics():
    api_rows = [{
        "keys": ["2026-05-25", "q", "p", "us", "DESKTOP"],
        # no clicks/impressions/ctr/position
    }]
    rows = gsc_pull.parse_rows(api_rows, "ex", "ex", "acc", datetime.now(timezone.utc))
    assert len(rows) == 1
    assert rows[0]["clicks"] == 0
    assert rows[0]["impressions"] == 0


def test_write_partitions_creates_monthly_files(tmp_path, monkeypatch):
    monkeypatch.setattr(gsc_pull, "GSC_DIR", tmp_path)
    rows = [
        {"date": date(2026, 5, 25), "site_url": "x", "host": "x.com",
         "query": "q1", "page": "p", "country": "us", "device": "DESKTOP",
         "clicks": 1, "impressions": 10, "ctr": 0.1, "position": 5.0,
         "account": "a", "pulled_at": datetime.now(timezone.utc)},
        {"date": date(2026, 6, 1), "site_url": "x", "host": "x.com",
         "query": "q2", "page": "p", "country": "us", "device": "MOBILE",
         "clicks": 0, "impressions": 5, "ctr": 0.0, "position": 8.0,
         "account": "a", "pulled_at": datetime.now(timezone.utc)},
    ]
    written = gsc_pull.write_partitions(rows, "x.com")
    assert set(written) == {"2026-05", "2026-06"}
    assert (tmp_path / "x.com" / "2026-05.parquet").exists()
    assert (tmp_path / "x.com" / "2026-06.parquet").exists()


def test_write_partitions_overwrites_same_date_on_rerun(tmp_path, monkeypatch):
    """Re-pulling the same date must not double rows."""
    monkeypatch.setattr(gsc_pull, "GSC_DIR", tmp_path)
    base = {
        "date": date(2026, 5, 25), "site_url": "x", "host": "x.com",
        "query": "q1", "page": "p", "country": "us", "device": "DESKTOP",
        "clicks": 1, "impressions": 10, "ctr": 0.1, "position": 5.0,
        "account": "a", "pulled_at": datetime.now(timezone.utc),
    }
    gsc_pull.write_partitions([base], "x.com")
    gsc_pull.write_partitions([{**base, "clicks": 7}], "x.com")  # same key, updated metric

    tbl = pq.read_table(tmp_path / "x.com" / "2026-05.parquet")
    assert tbl.num_rows == 1
    assert tbl.column("clicks").to_pylist() == [7]  # newer value wins


def test_write_partitions_preserves_other_dates_in_same_month(tmp_path, monkeypatch):
    """Refreshing day 25 must leave day 26 in the same month file intact."""
    monkeypatch.setattr(gsc_pull, "GSC_DIR", tmp_path)
    base = {
        "site_url": "x", "host": "x.com", "query": "q", "page": "p",
        "country": "us", "device": "DESKTOP", "clicks": 1, "impressions": 10,
        "ctr": 0.1, "position": 5.0, "account": "a",
        "pulled_at": datetime.now(timezone.utc),
    }
    gsc_pull.write_partitions([
        {**base, "date": date(2026, 5, 25), "clicks": 1},
        {**base, "date": date(2026, 5, 26), "clicks": 2},
    ], "x.com")
    # Refresh just day 25 with a new value
    gsc_pull.write_partitions([{**base, "date": date(2026, 5, 25), "clicks": 99}], "x.com")

    tbl = pq.read_table(tmp_path / "x.com" / "2026-05.parquet")
    by_date = dict(zip(tbl.column("date").to_pylist(), tbl.column("clicks").to_pylist()))
    assert by_date[date(2026, 5, 25)] == 99
    assert by_date[date(2026, 5, 26)] == 2  # untouched
