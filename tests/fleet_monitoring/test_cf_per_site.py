"""Tests for cf_per_site — parsers exercised directly with real CF response shapes.

Fixtures mirror what Cloudflare's GraphQL actually returns:
- httpRequests1dGroups: per-day groups with sum.{requests,threats,countryMap}
- httpRequestsAdaptiveGroups: per-row {count, dimensions} (NOT sum.requests)
"""
import asyncio

from projects.fleet_monitoring.cf_per_site import (
    parse_country_breakdown, parse_requests_threats_daily,
    parse_adaptive_counts, ua_looks_like_bot, fetch_all_for_zone,
    COUNTRY_THREATS_QUERY, _ADAPTIVE_DAY_QUERY)


# --- httpRequests1dGroups fixture: 2 days, nested countryMap -----------------

def _1d_groups_raw():
    return {"data": {"viewer": {"zones": [{"httpRequests1dGroups": [
        {"dimensions": {"date": "2026-05-18"},
         "sum": {"requests": 1000, "threats": 40, "countryMap": [
             {"clientCountryName": "US", "requests": 800, "bytes": 5_000_000},
             {"clientCountryName": "DE", "requests": 200, "bytes": 1_000_000}]}},
        {"dimensions": {"date": "2026-05-19"},
         "sum": {"requests": 1200, "threats": 10, "countryMap": [
             {"clientCountryName": "US", "requests": 900, "bytes": 6_000_000},
             {"clientCountryName": "BR", "requests": 300, "bytes": 2_000_000}]}},
    ]}]}}}


def test_parse_country_breakdown_aggregates_countrymap_across_days():
    out = parse_country_breakdown(_1d_groups_raw())
    # US = 800 + 900 = 1700; DE = 200; BR = 300 — sorted desc
    assert out == [
        {"country": "US", "requests": 1700, "bytes": 11_000_000},
        {"country": "BR", "requests": 300,  "bytes": 2_000_000},
        {"country": "DE", "requests": 200,  "bytes": 1_000_000},
    ]


def test_parse_country_breakdown_handles_empty():
    assert parse_country_breakdown({}) == []
    assert parse_country_breakdown({"data": {"viewer": {"zones": []}}}) == []


def test_parse_requests_threats_daily_extracts_per_day_totals():
    out = parse_requests_threats_daily(_1d_groups_raw())
    assert out == [
        {"date": "2026-05-18", "requests": 1000, "threats": 40},
        {"date": "2026-05-19", "requests": 1200, "threats": 10},
    ]


def test_parse_requests_threats_daily_sorts_oldest_first():
    raw = {"data": {"viewer": {"zones": [{"httpRequests1dGroups": [
        {"dimensions": {"date": "2026-05-19"}, "sum": {"requests": 5, "threats": 0}},
        {"dimensions": {"date": "2026-05-17"}, "sum": {"requests": 3, "threats": 0}},
    ]}]}}}
    out = parse_requests_threats_daily(raw)
    assert [r["date"] for r in out] == ["2026-05-17", "2026-05-19"]


# --- httpRequestsAdaptiveGroups fixture: count + dimensions -----------------

def test_parse_adaptive_counts_extracts_value_count_pairs():
    raw = {"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": [
        {"count": 8000, "dimensions": {"clientRequestPath": "/wp-login.php"}},
        {"count": 1500, "dimensions": {"clientRequestPath": "/"}},
    ]}]}}}
    assert parse_adaptive_counts(raw, "clientRequestPath") == [
        ("/wp-login.php", 8000), ("/", 1500)]


def test_parse_adaptive_counts_skips_null_dimension():
    raw = {"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": [
        {"count": 10, "dimensions": {"userAgent": None}},
        {"count": 20, "dimensions": {"userAgent": "Mozilla/5.0"}},
    ]}]}}}
    assert parse_adaptive_counts(raw, "userAgent") == [("Mozilla/5.0", 20)]


def test_parse_adaptive_counts_handles_empty():
    assert parse_adaptive_counts({}, "clientRequestPath") == []


# --- UA bot heuristic -------------------------------------------------------

def test_ua_looks_like_bot_flags_known_crawlers():
    assert ua_looks_like_bot("Mozilla/5.0 (compatible; ClaudeBot/1.0)")
    assert ua_looks_like_bot("GPTBot/1.1")
    assert ua_looks_like_bot("Mozilla/5.0 (compatible; bingbot/2.0)")
    assert ua_looks_like_bot("python-requests/2.31.0")
    assert ua_looks_like_bot("curl/8.4.0")


def test_ua_looks_like_bot_passes_real_browsers():
    assert not ua_looks_like_bot(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    assert not ua_looks_like_bot(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Safari/604.1")


def test_ua_looks_like_bot_handles_blank():
    assert not ua_looks_like_bot("")
    assert not ua_looks_like_bot(None)


# --- query templates --------------------------------------------------------

def test_country_threats_query_uses_1dgroups_with_threats_and_countrymap():
    assert "httpRequests1dGroups" in COUNTRY_THREATS_QUERY
    assert "threats" in COUNTRY_THREATS_QUERY
    assert "countryMap" in COUNTRY_THREATS_QUERY


def test_adaptive_day_query_uses_count_not_sum_requests():
    """Regression pin: adaptive groups have `count`, NOT `sum { requests }`.
    The original implementation used the wrong schema and every query 500'd."""
    assert "count" in _ADAPTIVE_DAY_QUERY
    assert "sum_requests_DESC" not in _ADAPTIVE_DAY_QUERY
    assert "orderBy: [count_DESC]" in _ADAPTIVE_DAY_QUERY
    assert "limit: %d" in _ADAPTIVE_DAY_QUERY      # adaptive requires a limit arg


# --- orchestrator -----------------------------------------------------------

def test_fetch_all_for_zone_assembles_block_from_both_datasets():
    """One 1dGroups call + 7 path days + 7 UA days. Verifies wiring + merge."""
    calls = {"1d": 0, "path": 0, "ua": 0}

    class FakeClient:
        async def graphql(self, query):
            if "httpRequests1dGroups" in query:
                calls["1d"] += 1
                return _1d_groups_raw()
            if "clientRequestPath" in query:
                calls["path"] += 1
                return {"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": [
                    {"count": 100, "dimensions": {"clientRequestPath": "/"}}]}]}}}
            if "userAgent" in query:
                calls["ua"] += 1
                return {"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": [
                    {"count": 50, "dimensions": {"userAgent": "GPTBot/1.1"}},
                    {"count": 30, "dimensions": {"userAgent": "Mozilla/5.0 Chrome/120"}}]}]}}}
            return {}

    out = asyncio.run(fetch_all_for_zone(FakeClient(), zone_id="z1"))
    assert calls == {"1d": 1, "path": 7, "ua": 7}
    assert out["country_window_days"] == 30
    assert out["traffic_window_days"] == 7
    assert "fetched_at" in out
    # countries + threats trend come from the 1dGroups response
    assert out["countries"][0]["country"] == "US"
    assert out["requests_threats_daily"][0]["threats"] == 40
    # 7d total = sum of requests across the (<=7) days present = 1000 + 1200
    assert out["total_requests_7d"] == 2200
    # paths merged across 7 identical days: 100 * 7 = 700
    assert out["top_paths"][0] == {
        "path": "/", "requests": 700,
        "pct_of_total": round(700 / 2200 * 100, 1)}
    # UAs merged + is_bot inferred
    ua_by_name = {u["ua"]: u for u in out["top_uas"]}
    assert ua_by_name["GPTBot/1.1"]["is_bot"] is True
    assert ua_by_name["GPTBot/1.1"]["requests"] == 350      # 50 * 7
    assert ua_by_name["Mozilla/5.0 Chrome/120"]["is_bot"] is False


def test_fetch_all_for_zone_failure_isolated_on_country_query():
    class BoomClient:
        async def graphql(self, query):
            raise RuntimeError("CF down")

    out = asyncio.run(fetch_all_for_zone(BoomClient(), zone_id="z1"))
    assert out["error"] is True
    assert out["countries"] == []
    assert out["requests_threats_daily"] == []
    assert out["top_paths"] == []
    assert out["top_uas"] == []


def test_fetch_all_for_zone_survives_per_day_path_failures():
    """A failing adaptive day is skipped, not fatal — block still returns."""
    class FlakyClient:
        async def graphql(self, query):
            if "httpRequests1dGroups" in query:
                return _1d_groups_raw()
            raise RuntimeError("adaptive query failed")

    out = asyncio.run(fetch_all_for_zone(FlakyClient(), zone_id="z1"))
    assert "error" not in out                       # country query succeeded
    assert out["countries"][0]["country"] == "US"
    assert out["top_paths"] == []                   # all 7 path days failed
    assert out["top_uas"] == []
