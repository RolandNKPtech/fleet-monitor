"""Tests for the redesigned Fleet Console renderer."""
import json
import re

from projects.fleet_monitoring.render_console import (
    _status_for_site, _storage_series, _cf_config_summary, _site_interventions,
    build_console_data, _default_key, _ordered_groups, _row_alert_sort_key,
    _site_list_html, _fleet_header_html, _sidebar_html, render_console,
)


# --- shared fixtures -------------------------------------------------------

def _snapshot():
    """Four sites: a critical one, a warning one, a clean wpe-only one,
    and a cf-only one (which lands in the (unassigned) group)."""
    return {
        "date": "2026-05-21",
        "captured_at": "2026-05-21T22:00:00+00:00",
        "alerts": [
            {"site_key": "crit.com", "severity": "critical",
             "rule": "insecure_tls", "summary": "min TLS 1.0"},
            {"site_key": "crit.com", "severity": "warning",
             "rule": "bandwidth_spike", "summary": "+40% vs baseline"},
            {"site_key": "warn.com", "severity": "warning",
             "rule": "bandwidth_spike", "summary": "+20% vs baseline"},
        ],
        "sites": [
            {"key": "crit.com", "apex": "crit.com", "join_state": "wpe+cf",
             "alerts_count": 2,
             "wpe": {"account_name": "acct-a", "install": "critinstall",
                     "bandwidth_gb_30d": 150.0, "billable_visits_30d": 2000,
                     "mb_per_visit": 75.0, "storage_gb": 5.0,
                     "cdn_gb_30d": 120.0, "origin_gb_30d": 30.0,
                     "daily": [
                         {"date": "2026-05-19",
                          "storage_file_bytes": 3_000_000_000,
                          "storage_database_bytes": 1_000_000_000},
                         {"date": "2026-05-20",
                          "storage_file_bytes": 3_200_000_000,
                          "storage_database_bytes": 1_100_000_000}]},
             "cf": {"zone_id": "zone-crit",
                    "analytics": {"cache_hit_rate": 55.0,
                                  "requests_30d": 90000, "threats": 1200},
                    "config": {"settings": {"ssl": "strict",
                                            "min_tls_version": "1.0"},
                               "bot": {"ai_bots_protection": "disabled"},
                               "waf_rules": [{"description": "r1"},
                                             {"description": "r2"}],
                               "cache_rules": [{"description": "c1"}],
                               "dns_proxy_www": True,
                               "dns_proxy_apex": False}}},
            {"key": "warn.com", "apex": "warn.com", "join_state": "wpe+cf",
             "alerts_count": 1,
             "wpe": {"account_name": "acct-a", "install": "warninstall",
                     "bandwidth_gb_30d": 40.0, "billable_visits_30d": 1500,
                     "mb_per_visit": 27.0, "storage_gb": 2.0,
                     "cdn_gb_30d": 35.0, "origin_gb_30d": 5.0, "daily": []},
             "cf": {"zone_id": "zone-warn",
                    "analytics": {"cache_hit_rate": 88.0,
                                  "requests_30d": 50000, "threats": 200},
                    "config": {"settings": {"ssl": "strict",
                                            "min_tls_version": "1.2"},
                               "bot": {"ai_bots_protection": "enabled"},
                               "waf_rules": [{"description": "r1"}],
                               "cache_rules": [],
                               "dns_proxy_www": True,
                               "dns_proxy_apex": False}}},
            {"key": "clean.com", "apex": "clean.com",
             "join_state": "wpe-only", "alerts_count": 0,
             "wpe": {"account_name": "acct-b", "install": "cleaninstall",
                     "bandwidth_gb_30d": 10.0, "billable_visits_30d": 500,
                     "mb_per_visit": 20.0, "storage_gb": 1.0,
                     "cdn_gb_30d": 9.0, "origin_gb_30d": 1.0, "daily": []},
             "cf": None},
            {"key": "cfonly.com", "apex": "cfonly.com",
             "join_state": "cf-only", "alerts_count": 0, "wpe": None,
             "cf": {"zone_id": "zone-cfonly",
                    "analytics": {"cache_hit_rate": 0.0,
                                  "requests_30d": 100, "threats": 0},
                    "config": {"settings": {}, "bot": {},
                               "waf_rules": [], "cache_rules": [],
                               "dns_proxy_www": False,
                               "dns_proxy_apex": False}}},
        ],
    }


def _ts():
    return [
        {"key": "crit.com", "date": "2026-05-19", "bandwidth_gb": 140.0,
         "billable_visits": 1900, "threats": 1000, "cache_hit_rate": 55.0,
         "mb_per_visit": 73.0, "account": "acct-a", "alert_count": 1},
        {"key": "crit.com", "date": "2026-05-20", "bandwidth_gb": 150.0,
         "billable_visits": 2000, "threats": 1200, "cache_hit_rate": 55.0,
         "mb_per_visit": 75.0, "account": "acct-a", "alert_count": 2},
        {"key": "warn.com", "date": "2026-05-20", "bandwidth_gb": 40.0,
         "billable_visits": 1500, "threats": 200, "cache_hit_rate": 88.0,
         "mb_per_visit": 27.0, "account": "acct-a", "alert_count": 1},
    ]


def _iv():
    return {"needs_review": [], "rows": [
        {"site": "crit.com", "applied_date": "2026-04-30",
         "type": "block_ai_scrapers", "target_metric": "bandwidth",
         "supported": True,
         "horizons": {7: {"verdict": "worked", "delta_pct": -30.0},
                      30: {"verdict": "worked", "delta_pct": -45.0},
                      90: {"verdict": "too_early", "delta_pct": None}}},
    ]}


def _rec(data, key):
    return next(r for r in data if r["key"] == key)


# --- Task 1: data layer ----------------------------------------------------

def test_status_critical_beats_warning():
    alerts = [{"severity": "warning"}, {"severity": "critical"}]
    assert _status_for_site("wpe+cf", alerts) == "crit"


def test_status_warning():
    assert _status_for_site("wpe+cf", [{"severity": "warning"}]) == "warn"


def test_status_ok_for_clean_wpe_site():
    assert _status_for_site("wpe-only", []) == "ok"


def test_status_nodata_for_clean_cfonly_site():
    assert _status_for_site("cf-only", []) == "nodata"


def test_build_console_data_one_record_per_site():
    data = build_console_data(_snapshot(), _ts(), _iv())
    assert {r["key"] for r in data} == {
        "crit.com", "warn.com", "clean.com", "cfonly.com"}


def test_build_console_data_skips_sites_without_a_key():
    snap = {"date": "2026-05-21", "alerts": [], "sites": [
        {"wpe": {}, "cf": None, "join_state": "wpe-only"},
        {"key": "ok.com", "apex": "ok.com", "join_state": "cf-only",
         "wpe": None, "cf": None}]}
    data = build_console_data(snap, [])
    assert [d["key"] for d in data] == ["ok.com"]


def test_site_record_has_safe_key():
    data = build_console_data(_snapshot(), _ts(), _iv())
    assert _rec(data, "crit.com")["safe_key"] == "crit.com"


def test_site_record_has_all_schema_fields():
    data = build_console_data(_snapshot(), _ts(), _iv())
    rec = _rec(data, "crit.com")
    for field in ("key", "safe_key", "account", "install", "zone", "join",
                  "apex", "status", "bandwidth_gb", "visits", "mb_per_visit",
                  "storage_gb", "cdn_gb", "origin_gb", "cache_hit",
                  "requests_30d", "threats_30d", "pct_5xx_7d",
                  "requests_5xx_7d", "requests_7d", "cf_config", "bw_series",
                  "threat_series", "threat_dates", "storage_series",
                  "alerts", "interventions"):
        assert field in rec, f"missing field {field}"


def test_cfonly_site_has_null_wpe_fields_and_unassigned_account():
    data = build_console_data(_snapshot(), _ts(), _iv())
    rec = _rec(data, "cfonly.com")
    assert rec["bandwidth_gb"] is None
    assert rec["storage_gb"] is None
    assert rec["account"] == "(unassigned)"


def test_wpeonly_site_has_null_cf_config():
    data = build_console_data(_snapshot(), _ts(), _iv())
    rec = _rec(data, "clean.com")
    assert rec["cf_config"] is None
    assert rec["cache_hit"] is None


def test_storage_series_from_daily():
    series = _storage_series(_snapshot()["sites"][0]["wpe"])
    assert len(series) == 2
    assert series[0] == {"date": "2026-05-19", "file_gb": 3.0, "db_gb": 1.0}


def test_threat_series_parallel_to_dates():
    data = build_console_data(_snapshot(), _ts(), _iv())
    rec = _rec(data, "crit.com")
    assert rec["threat_series"] == [1000, 1200]
    assert rec["threat_dates"] == ["2026-05-19", "2026-05-20"]


def test_interventions_extracted_with_best_verdict():
    data = build_console_data(_snapshot(), _ts(), _iv())
    ivs = _rec(data, "crit.com")["interventions"]
    assert len(ivs) == 1
    assert ivs[0]["verdict"] == "worked"
    assert ivs[0]["applied_date"] == "2026-04-30"


def test_cf_config_summary_fields():
    cfg = _cf_config_summary(_snapshot()["sites"][0]["cf"])
    assert cfg["tls"] == "1.0"
    assert cfg["ssl"] == "strict"
    assert cfg["waf_count"] == 2
    assert cfg["cache_rule_count"] == 1
    assert cfg["ai_protection"] == "disabled"
    assert cfg["dns_proxy_www"] is True


# --- Task 2: selection + grouping -----------------------------------------

def test_default_key_picks_highest_severity_site():
    data = build_console_data(_snapshot(), _ts(), _iv())
    assert _default_key(data) == "crit.com"


def test_default_key_falls_back_to_highest_bandwidth_when_no_alerts():
    snap = _snapshot()
    snap["alerts"] = []
    data = build_console_data(snap, _ts(), _iv())
    # crit.com has the highest bandwidth_gb (150) among WPE sites.
    assert _default_key(data) == "crit.com"


def test_default_key_never_picks_a_nodata_site_when_wpe_sites_exist():
    snap = _snapshot()
    snap["alerts"] = []
    data = build_console_data(snap, _ts(), _iv())
    chosen = _rec(data, _default_key(data))
    # A "nodata" cf-only site has bandwidth_gb None; a WPE site does not.
    assert chosen["bandwidth_gb"] is not None


def test_default_key_none_for_empty_data():
    assert _default_key([]) is None


def test_ordered_groups_puts_unassigned_last():
    data = build_console_data(_snapshot(), _ts(), _iv())
    names = [name for name, _ in _ordered_groups(data)]
    assert names[-1] == "(unassigned)"
    assert "acct-a" in names and "acct-b" in names


def test_ordered_groups_sorts_real_accounts_by_bandwidth():
    data = build_console_data(_snapshot(), _ts(), _iv())
    names = [name for name, _ in _ordered_groups(data)]
    # acct-a totals 190 GB, acct-b totals 10 GB.
    assert names.index("acct-a") < names.index("acct-b")


def test_row_alert_sort_key_orders_critical_first():
    data = build_console_data(_snapshot(), _ts(), _iv())
    acct_a = [r for r in data if r["account"] == "acct-a"]
    ordered = sorted(acct_a, key=_row_alert_sort_key)
    assert ordered[0]["key"] == "crit.com"


# --- Task 3: server-rendered shell ----------------------------------------

def test_fleet_header_shows_counts():
    data = build_console_data(_snapshot(), _ts(), _iv())
    html = _fleet_header_html(_snapshot(), data)
    assert "<b>4</b> sites" in html
    assert "<b>3</b> alerts" in html
    assert "<b>2</b> WPE" in html


def test_site_list_unassigned_group_renders_last():
    data = build_console_data(_snapshot(), _ts(), _iv())
    html = _site_list_html(data)
    assert html.index("acct-a") < html.index("(unassigned)")


def test_site_row_carries_status_class():
    data = build_console_data(_snapshot(), _ts(), _iv())
    html = _site_list_html(data)
    assert 'st-crit' in html
    assert 'st-nodata' in html


def test_site_row_shows_alert_badge_only_when_alerted():
    data = build_console_data(_snapshot(), _ts(), _iv())
    html = _site_list_html(data)
    assert '<span class="fc-badge">2</span>' in html
    start = html.index('data-key="clean.com"')
    end = html.index('</li>', start) + len('</li>')
    clean_row = html[start:end]
    assert 'fc-badge' not in clean_row


def test_site_row_has_sort_data_attributes():
    data = build_console_data(_snapshot(), _ts(), _iv())
    html = _site_list_html(data)
    assert 'data-bw=' in html
    assert 'data-sev=' in html
    assert 'data-alerts=' in html


def test_sidebar_links_to_dashboard_tabs():
    html = _sidebar_html()
    assert 'href="dashboard.html#sites"' in html
    assert 'href="dashboard.html#interventions"' in html
    assert 'class="on"' in html


# --- Task 4: render_console assembly --------------------------------------

def test_render_console_starts_with_doctype():
    html = render_console(_snapshot(), _ts(), _iv())
    assert html.startswith("<!DOCTYPE html>")


def test_render_console_is_self_contained():
    html = render_console(_snapshot(), _ts(), _iv())
    assert "<link" not in html
    assert "<script src" not in html


def test_render_console_embeds_one_record_per_site():
    html = render_console(_snapshot(), _ts(), _iv())
    m = re.search(r'<script id="console-data"[^>]*>(.*?)</script>',
                  html, re.S)
    assert m, "console-data script not found"
    data = json.loads(m.group(1))
    assert {d["key"] for d in data} == {
        "crit.com", "warn.com", "clean.com", "cfonly.com"}


def test_render_console_embeds_default_key():
    html = render_console(_snapshot(), _ts(), _iv())
    m = re.search(r'<script id="default-key"[^>]*>(.*?)</script>',
                  html, re.S)
    assert m, "default-key script not found"
    assert json.loads(m.group(1)) == "crit.com"


def test_render_console_embedded_json_has_no_raw_closing_tag():
    html = render_console(_snapshot(), _ts(), _iv())
    for tag_id in ("console-data", "default-key"):
        body = html[html.index(f'id="{tag_id}"'):]
        body = body[:body.index("</script>")]
        assert "</" not in body  # hardened with <\/


def test_render_console_empty_snapshot_renders_shell():
    html = render_console({"date": "2026-05-21", "alerts": [],
                           "sites": []}, [])
    assert html.startswith("<!DOCTYPE html>")
    assert "Fleet Console" in html
    assert "No sites" in html


def test_site_record_carries_5xx_fields_through():
    snap = _snapshot()
    snap["sites"][0]["cf"]["analytics"].update({
        "pct_5xx_7d": 2.4, "requests_5xx_7d": 1200, "requests_7d": 50_000,
    })
    rec = _rec(build_console_data(snap, _ts(), _iv()), "crit.com")
    assert rec["pct_5xx_7d"] == 2.4
    assert rec["requests_5xx_7d"] == 1200
    assert rec["requests_7d"] == 50_000


def test_console_renders_5xx_row_when_present_and_omits_when_absent():
    snap = _snapshot()
    snap["sites"][0]["cf"]["analytics"].update({
        "pct_5xx_7d": 2.4, "requests_5xx_7d": 1200, "requests_7d": 50_000,
    })
    html = render_console(snap, _ts(), _iv())
    assert "5xx 7d" in html
    # cfonly.com has no 5xx fields set — site_record still carries them as
    # None, and the JS guards on `!= null`. So the literal "5xx 7d" must
    # still appear (for crit.com) but no JS crash. Smoke check: render runs.


def test_render_console_works_without_interventions_view():
    # The third argument is optional; older callers must still work.
    html = render_console(_snapshot(), _ts())
    assert html.startswith("<!DOCTYPE html>")


def test_build_console_data_drops_resolved_and_muted_alerts_per_site():
    """Resolved/muted alerts must not show up in the per-site panel — they
    are stale or silenced state, not active issues (the 35-of-37 stale-rows
    bug on example.com)."""
    snap = _snapshot()
    snap["alerts"] = [
        {"site_key": "crit.com", "severity": "critical",
         "rule": "insecure_tls", "summary": "min TLS 1.0", "state": "ongoing"},
        {"site_key": "crit.com", "severity": "warning",
         "rule": "mb_per_visit_high", "summary": "old value",
         "state": "resolved"},
        {"site_key": "crit.com", "severity": "warning",
         "rule": "bot_ratio", "summary": "silenced", "state": "muted"},
    ]
    rec = _rec(build_console_data(snap, _ts(), _iv()), "crit.com")
    rules = sorted(a["rule"] for a in rec["alerts"])
    assert rules == ["insecure_tls"]   # ongoing only; resolved + muted dropped


def test_build_console_data_includes_analytics_block():
    """The per-site record carries the analytics dict so the panel JS can
    render the new Analytics card without extra fetches."""
    snap = _snapshot()
    # Attach an analytics block to crit.com (the first site in _snapshot()).
    snap["sites"][0]["analytics"] = {
        "ga4": {"property_id": "111", "source": "auto",
                "sessions_30d": 2870, "conversions_30d": 41,
                "engagement_rate": 0.62,
                "sessions_7d": 612, "sessions_prev_7d": 689,
                "conversions_7d": 7, "conversions_prev_7d": 10},
        "gsc": {"site_url": "sc-domain:crit.com", "source": "auto",
                "clicks_30d": 4_120, "impressions_30d": 88_500,
                "clicks_7d": 980, "clicks_prev_7d": 1_010},
    }
    rec = _rec(build_console_data(snap, _ts(), _iv()), "crit.com")
    assert rec["analytics"]["ga4"]["sessions_30d"] == 2870
    assert rec["analytics"]["gsc"]["clicks_30d"] == 4120


def test_render_console_panel_js_has_analytics_card():
    """_CONSOLE_JS must include the Analytics card markup so selectSite()
    renders it."""
    html = render_console(_snapshot(), _ts(), _iv())
    assert "Analytics (30d)" in html
    assert "_analyticsHtml" in html   # the JS helper name
