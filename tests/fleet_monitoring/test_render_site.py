from projects.fleet_monitoring.render_site import safe_key
from projects.fleet_monitoring.render_site import render_site_page


def test_safe_key_lowercases_and_keeps_domain_chars():
    assert safe_key("Advancedplasticde.COM") == "advancedplasticde.com"


def test_safe_key_replaces_unsafe_chars_with_dash():
    assert safe_key("foo bar/baz?.com") == "foo-bar-baz-.com"


def test_safe_key_is_idempotent():
    once = safe_key("Example-Site.com")
    twice = safe_key(once)
    assert once == twice == "example-site.com"


def _wpe_cf_site():
    return {
        "key": "advancedplasticde.com",
        "apex": "advancedplasticde.com",
        "join_state": "wpe+cf",
        "wpe": {
            "install": "advplastic", "account": "uuid-1", "account_name": "acctA",
            "bandwidth_gb_30d": 113.8, "billable_visits_30d": 3313,
            "mb_per_visit": 34.4, "storage_gb": 12.0,
        },
        "cf": {
            "zone_id": "z1",
            "config": {"settings": {"ssl": "full"}},
            "analytics": {"requests_30d": 1297699, "threats": 25, "cache_hit_rate": 48.6},
        },
        "alerts_count": 0,
    }


def _snap_with(site):
    return {"date": "2026-05-19", "captured_at": "2026-05-19T22:00:00+00:00",
            "sites": [site],
            "alerts": [
                {"site_key": site["key"], "rule": "bandwidth_spike", "severity": "warning",
                 "summary": "BW +18% vs baseline", "detail": {}, "state": "new",
                 "mute_reason": None, "fingerprint": "x:y:z"},
                {"site_key": "other.com", "rule": "config_drift", "severity": "critical",
                 "summary": "elsewhere", "detail": {}, "state": "new",
                 "mute_reason": None, "fingerprint": "a:b:c"},
            ]}


def test_render_site_page_wpe_cf_includes_header_stats_alerts():
    site = _wpe_cf_site()
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    # Header: title + back link + refresh button
    assert "advancedplasticde.com" in html
    assert "Back to fleet" in html
    assert "Refresh this site" in html
    assert "join-pill" in html and "wpe+cf" in html
    # Quick stats card row — all 5 numbers visible
    assert "113.8" in html or "113" in html        # bandwidth
    assert "3,313" in html or "3313" in html        # visits
    assert "34.4" in html                            # MB/visit
    assert "48.6" in html or "48" in html            # cache hit %
    assert "25" in html                              # threats
    # Active alerts — only THIS site's alert, not the other one
    assert "BW +18%" in html
    assert "elsewhere" not in html


def test_render_site_page_wpe_only_shows_notice_and_skips_cf_stats():
    site = {"key": "noccf.com", "apex": "noccf.com", "join_state": "wpe-only",
            "wpe": {"install": "x", "account_name": "acctC",
                    "bandwidth_gb_30d": 50.0, "billable_visits_30d": 1000,
                    "mb_per_visit": 50.0, "storage_gb": 5.0},
            "cf": None, "alerts_count": 0}
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Not behind our Cloudflare" in html
    assert "50.0" in html                            # WPE bandwidth still shows


def test_render_site_page_cf_only_shows_notice_and_skips_wpe_stats():
    site = {"key": "cfonly.com", "apex": "cfonly.com", "join_state": "cf-only",
            "wpe": None,
            "cf": {"zone_id": "z2", "config": {"settings": {}},
                   "analytics": {"requests_30d": 100, "threats": 0, "cache_hit_rate": 50.0}},
            "alerts_count": 0}
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Not on WP Engine" in html
    assert "50.0" in html or "50" in html            # cache hit % still shows


def test_render_site_page_includes_cf_config_summary_for_wpe_cf():
    site = _wpe_cf_site()
    site["cf"]["config"] = {
        "settings": {"ssl": "full", "security_level": "medium",
                     "always_use_https": "on"},
        "bot": {"fight_mode": True, "ai_bots_protection": "block"},
        "waf_rules": [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}],
        "cache_rules": [{"id": "c1"}],
        "dns_proxy_apex": False,
        "dns_proxy_www": True,
    }
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "CF Configuration" in html
    assert "full" in html.lower()        # SSL mode
    assert "medium" in html.lower()      # security level
    assert "WAF rules" in html and "3" in html
    assert "Cache rules" in html and "1" in html
    assert "apex proxy" in html.lower() and "www proxy" in html.lower()


def test_render_site_page_includes_bandwidth_mini_chart_when_history_present():
    site = _wpe_cf_site()
    rows = [
        {"date": "2026-05-15", "key": site["key"], "bandwidth_gb": 100.0},
        {"date": "2026-05-16", "key": site["key"], "bandwidth_gb": 110.0},
        {"date": "2026-05-17", "key": site["key"], "bandwidth_gb": 105.0},
        {"date": "2026-05-18", "key": "other.com", "bandwidth_gb": 999.0},  # filtered out
    ]
    html = render_site_page(site, _snap_with(site), timeseries_rows=rows)
    assert "Bandwidth trend" in html
    assert "<svg" in html
    assert 'class="sp-chart"' in html       # shared axis-grid chart
    assert "sp-grid" in html                # gridlines rendered
    assert "sp-axis" in html                # Y/X axis labels rendered


def test_render_site_page_bandwidth_chart_empty_state_with_single_point():
    site = _wpe_cf_site()
    rows = [{"date": "2026-05-15", "key": site["key"], "bandwidth_gb": 100.0}]
    html = render_site_page(site, _snap_with(site), timeseries_rows=rows)
    assert "Bandwidth trend" in html
    assert "history building" in html.lower() or "need" in html.lower()


def _per_site_block():
    """Per-site block in the CURRENT shape — 30d country+threats, 7d paths/UAs.

    requests_threats_daily here has 3 days summing to 100,000 requests, used
    as the 30d denominator for the country %.
    """
    return {
        "fetched_at": "2026-05-19T22:00:00+00:00",
        "country_window_days": 30, "traffic_window_days": 7,
        "total_requests_7d": 100_000,
        "countries": [], "top_paths": [], "top_uas": [],
        "requests_threats_daily": [
            {"date": "2026-05-17", "requests": 30_000, "threats": 1200},
            {"date": "2026-05-18", "requests": 35_000, "threats": 800},
            {"date": "2026-05-19", "requests": 35_000, "threats": 400},
        ],
    }


def test_render_site_page_requests_threats_chart_shows_when_per_site_data_present():
    site = _wpe_cf_site()
    site["cf"]["per_site"] = _per_site_block()
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Requests" in html and "threats" in html.lower()
    assert "<svg" in html
    assert 'class="sp-chart"' in html       # shared axis-grid chart
    assert "#2563eb" in html and "#dc2626" in html   # requests + threats lines
    # Total threats disclosed (1200 + 800 + 400 = 2,400)
    assert "2,400 threats" in html


def test_render_site_page_requests_threats_empty_state_when_no_per_site_block():
    site = _wpe_cf_site()
    # site["cf"]["per_site"] not set
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "per-site analytics not yet collected" in html.lower()


def test_render_site_page_top_countries_horizontal_bars():
    site = _wpe_cf_site()
    # _per_site_block()'s requests_threats_daily sums to 100,000 requests —
    # that is the 30d denominator the country % is computed against.
    site["cf"]["per_site"] = _per_site_block() | {"countries": [
        {"country": "US", "requests": 80_000, "bytes": 500_000_000},
        {"country": "DE", "requests": 15_000, "bytes": 80_000_000},
        {"country": "BR", "requests":  5_000, "bytes": 20_000_000},
    ]}
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Top countries" in html or "Top 20 countries" in html
    assert "US" in html and "DE" in html and "BR" in html
    assert "country-bar" in html
    assert "80.0%" in html or "80%" in html       # 80,000 / 100,000 total


def test_render_site_page_top_paths_and_uas_tables():
    site = _wpe_cf_site()
    site["cf"]["per_site"] = _per_site_block() | {
        "top_paths": [
            {"path": "/wp-login.php", "requests": 8000, "pct_of_total": 80.0},
            {"path": "/",             "requests": 1500, "pct_of_total": 15.0},
        ],
        "top_uas": [
            {"ua": "Mozilla/5.0 ...", "requests": 6000, "is_bot": False, "pct_of_total": 60.0},
            {"ua": "ClaudeBot/1.0",   "requests": 4000, "is_bot": True,  "pct_of_total": 40.0},
        ],
    }
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Top paths" in html and "/wp-login.php" in html
    assert "Top user agents" in html and "ClaudeBot" in html
    # Bot flag rendered for UA
    assert "ua-bot" in html or "BOT" in html


import tempfile
from pathlib import Path
from projects.fleet_monitoring.render_site import write_all_site_pages


def test_render_site_page_footer_names_source_and_fetched_at():
    site = _wpe_cf_site()
    site["cf"]["per_site"] = _per_site_block()
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "httpRequests1dGroups" in html
    assert "httpRequestsAdaptiveGroups" in html
    assert "30d" in html and "7d" in html
    assert "2026-05-19T22:00:00+00:00" in html


def test_write_all_site_pages_writes_one_file_per_site(tmp_path, monkeypatch):
    import projects.fleet_monitoring.render_site as rs
    monkeypatch.setattr(rs, "SITES_DIR", tmp_path / "sites")
    snap = {"date": "2026-05-19", "captured_at": "2026-05-19T22:00:00+00:00",
            "alerts": [], "sites": [
                _wpe_cf_site(),
                {"key": "noccf.com", "join_state": "wpe-only",
                 "wpe": {"install": "x", "account_name": "acctC",
                         "bandwidth_gb_30d": 50.0, "billable_visits_30d": 1000,
                         "mb_per_visit": 50.0, "storage_gb": 5.0},
                 "cf": None, "alerts_count": 0},
            ]}
    n = write_all_site_pages(snap, timeseries_rows=[])
    assert n == 2
    files = sorted((tmp_path / "sites").glob("*.html"))
    names = [f.name for f in files]
    assert "advancedplasticde.com.html" in names
    assert "noccf.com.html" in names
    # Each file is openable HTML
    for f in files:
        assert f.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_compact_keeps_one_decimal_below_ten():
    from projects.fleet_monitoring.render_site import _compact
    assert _compact(0.79) == "0.8"
    assert _compact(1.02) == "1.0"
    assert _compact(9.4) == "9.4"
    # 10 and up stays integer; thousands/millions unchanged
    assert _compact(58) == "58"
    assert _compact(24_000) == "24k"
    assert _compact(1_300_000) == "1.3M"


def _site_with_storage_daily(days):
    """A wpe+cf site whose wpe.daily carries `days` storage records."""
    site = _wpe_cf_site()
    site["wpe"]["daily"] = days
    return site


def test_storage_trend_section_renders_two_line_chart():
    days = [
        {"date": "2026-05-17", "storage_file_bytes": 4_000_000_000,
         "storage_database_bytes": 200_000_000},
        {"date": "2026-05-18", "storage_file_bytes": 4_300_000_000,
         "storage_database_bytes": 210_000_000},
        {"date": "2026-05-19", "storage_file_bytes": 4_900_000_000,
         "storage_database_bytes": 230_000_000},
    ]
    site = _site_with_storage_daily(days)
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Storage trend" in html
    assert 'class="sp-chart"' in html
    assert "#2563eb" in html and "#d97706" in html   # files + database lines
    assert "files" in html and "database" in html


def test_storage_trend_section_empty_state_with_one_day():
    site = _site_with_storage_daily([
        {"date": "2026-05-19", "storage_file_bytes": 4_000_000_000,
         "storage_database_bytes": 200_000_000}])
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Storage trend" in html
    assert "history building" in html.lower()


def test_storage_trend_section_absent_for_cf_only_site():
    site = {"key": "cfonly.com", "apex": "cfonly.com", "join_state": "cf-only",
            "wpe": None,
            "cf": {"zone_id": "z2", "config": {"settings": {}},
                   "analytics": {"requests_30d": 100, "threats": 0,
                                 "cache_hit_rate": 50.0}},
            "alerts_count": 0}
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Storage trend" not in html


def test_render_site_page_filters_resolved_and_muted_alerts():
    """The per-site page must not list resolved or muted alerts; only
    actively firing ones."""
    site = _wpe_cf_site()
    snap = {"date": "2026-05-19", "captured_at": "2026-05-19T22:00:00+00:00",
            "sites": [site],
            "alerts": [
                {"site_key": site["key"], "rule": "bandwidth_spike",
                 "severity": "warning", "summary": "ACTIVE-NOW",
                 "detail": {}, "state": "ongoing", "mute_reason": None,
                 "fingerprint": "x"},
                {"site_key": site["key"], "rule": "bot_ratio",
                 "severity": "warning", "summary": "STALE-RESOLVED",
                 "detail": {}, "state": "resolved", "mute_reason": None,
                 "fingerprint": "y"},
                {"site_key": site["key"], "rule": "config_drift",
                 "severity": "info", "summary": "SILENCED-MUTED",
                 "detail": {}, "state": "muted", "mute_reason": "intentional",
                 "fingerprint": "z"},
            ]}
    html = render_site_page(site, snap, timeseries_rows=[])
    assert "ACTIVE-NOW" in html
    assert "STALE-RESOLVED" not in html
    assert "SILENCED-MUTED" not in html


def test_render_site_page_shows_analytics_card_when_present():
    site = _wpe_cf_site()
    site["analytics"] = {
        "ga4": {"property_id": "111", "source": "auto",
                "sessions_30d": 2870, "conversions_30d": 41,
                "engagement_rate": 0.62,
                "sessions_7d": 612, "sessions_prev_7d": 689,
                "conversions_7d": 7, "conversions_prev_7d": 10},
        "gsc": {"site_url": "sc-domain:advancedplasticde.com",
                "source": "auto", "clicks_30d": 4_120,
                "impressions_30d": 88_500,
                "clicks_7d": 980, "clicks_prev_7d": 1_010},
    }
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Analytics (30d)" in html
    assert "2,870" in html or "2870" in html
    assert "4,120" in html or "4120" in html


def test_render_site_page_shows_no_analytics_empty_state_when_missing():
    site = _wpe_cf_site()
    site["analytics"] = {"ga4": None, "gsc": None}
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "No GA4 access for this site." in html
    assert "No GSC access for this site." in html


def test_render_site_page_shows_5xx_card_when_present():
    site = _wpe_cf_site()
    site["cf"]["analytics"].update({
        "pct_5xx_7d": 1.85, "requests_5xx_7d": 1_480, "requests_7d": 80_000,
    })
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "5xx rate 7d" in html
    assert "1.85%" in html
    assert "1,480" in html
    assert "80,000" in html


def test_render_site_page_skips_5xx_card_when_pct_is_none():
    site = _wpe_cf_site()   # no pct_5xx_7d set
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "5xx rate 7d" not in html


def test_render_site_page_shows_cert_expiry_card_when_present():
    site = _wpe_cf_site()
    site["cf"]["cert_expiry"] = {
        "min_days_until_expiry": 18,
        "earliest_expires_on": "2026-06-21",
        "earliest_issuer": "GoogleTrustServices",
        "active_pack_count": 1,
        "earliest_pack_id": "pack-1",
    }
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Cert expiry" in html
    assert "in 18 days" in html
    assert "2026-06-21" in html
    assert "GoogleTrustServices" in html


def test_render_site_page_cert_card_shows_expired_state_for_negative_days():
    site = _wpe_cf_site()
    site["cf"]["cert_expiry"] = {
        "min_days_until_expiry": -5,
        "earliest_expires_on": "2026-05-29",
        "earliest_issuer": "LetsEncrypt",
        "active_pack_count": 1,
    }
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "EXPIRED 5d ago" in html


def test_render_site_page_skips_cert_card_when_data_absent():
    site = _wpe_cf_site()   # no cert_expiry set
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Cert expiry" not in html


def test_render_site_page_shows_plan_card_when_cf_plan_present():
    site = _wpe_cf_site()
    site["cf"]["plan"] = {"name": "Pro Website", "price": 20,
                          "frequency": "monthly", "currency": "USD"}
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "CF plan" in html
    assert "Pro Website" in html
    assert "USD 20/mo" in html


def test_render_site_page_plan_card_shows_free_label_for_zero_price():
    site = _wpe_cf_site()
    site["cf"]["plan"] = {"name": "Free Website", "price": 0,
                          "frequency": "monthly", "currency": "USD"}
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "Free Website" in html
    assert "free" in html


def test_render_site_page_skips_plan_card_when_cf_plan_absent():
    site = _wpe_cf_site()   # no cf.plan
    html = render_site_page(site, _snap_with(site), timeseries_rows=[])
    assert "CF plan" not in html


# --- card colour-coding ---------------------------------------------------

def _snap_with_alerts(site, alerts):
    """Snapshot with a custom alert list (replaces _snap_with's default)."""
    return {"date": "2026-05-19", "captured_at": "2026-05-19T22:00:00+00:00",
            "sites": [site], "alerts": alerts}


def _alert(site_key, rule, severity, state="new"):
    return {"site_key": site_key, "rule": rule, "severity": severity,
            "state": state, "summary": "", "detail": {},
            "mute_reason": None, "fingerprint": f"{site_key}:{rule}"}


# The stylesheet defines `.ss-card-bad{...}` so the literal `ss-card-bad`
# always appears in the HTML. Match the applied class attribute instead.
_APPLIED_CARD_OK = 'class="ss-card ss-card-ok"'
_APPLIED_CARD_WATCH = 'class="ss-card ss-card-watch"'
_APPLIED_CARD_BAD = 'class="ss-card ss-card-bad"'
_APPLIED_VALUE_BAD = 'class="ss-value ss-value-bad"'
_APPLIED_PILL_WATCH = 'class="ss-pill ss-pill-watch"'


def test_stats_card_reads_ok_when_monitored_metric_has_no_alert():
    site = _wpe_cf_site()
    snap = _snap_with_alerts(site, alerts=[])
    html = render_site_page(site, snap, timeseries_rows=[])
    # Bandwidth + MB/visit + Cache hit + Threats — all monitored by rules,
    # and no rule is firing -> all "ok" green.
    assert _APPLIED_CARD_OK in html


def test_stats_card_reads_watch_when_rule_fires_warning():
    site = _wpe_cf_site()
    snap = _snap_with_alerts(site, alerts=[
        _alert(site["key"], "bandwidth_spike", "warning"),
    ])
    html = render_site_page(site, snap, timeseries_rows=[])
    assert _APPLIED_CARD_WATCH in html
    assert _APPLIED_PILL_WATCH in html


def test_stats_card_reads_bad_when_rule_fires_critical():
    site = _wpe_cf_site()
    snap = _snap_with_alerts(site, alerts=[
        _alert(site["key"], "mb_per_visit_high", "critical"),
    ])
    html = render_site_page(site, snap, timeseries_rows=[])
    assert _APPLIED_CARD_BAD in html
    assert _APPLIED_VALUE_BAD in html


def test_stats_card_critical_beats_warning_when_both_fire():
    site = _wpe_cf_site()
    snap = _snap_with_alerts(site, alerts=[
        _alert(site["key"], "bandwidth_spike", "warning"),
        _alert(site["key"], "bandwidth_spike", "critical"),
    ])
    html = render_site_page(site, snap, timeseries_rows=[])
    assert _APPLIED_CARD_BAD in html   # the critical drives the colour


def test_stats_card_ignores_resolved_and_muted_alerts():
    site = _wpe_cf_site()
    snap = _snap_with_alerts(site, alerts=[
        _alert(site["key"], "bandwidth_spike", "critical", state="resolved"),
        _alert(site["key"], "mb_per_visit_high", "critical", state="muted"),
    ])
    html = render_site_page(site, snap, timeseries_rows=[])
    assert _APPLIED_CARD_BAD not in html
    assert _APPLIED_VALUE_BAD not in html


def test_stats_card_ignores_other_sites_alerts():
    site = _wpe_cf_site()
    snap = _snap_with_alerts(site, alerts=[
        _alert("other.com", "bandwidth_spike", "critical"),
    ])
    html = render_site_page(site, snap, timeseries_rows=[])
    assert _APPLIED_CARD_BAD not in html


def test_unmonitored_cards_stay_neutral():
    # "Billable visits 30d" has no rule mapping — its card uses class="ss-card"
    # (no colour suffix). Find it and verify.
    site = _wpe_cf_site()
    snap = _snap_with_alerts(site, alerts=[])
    html = render_site_page(site, snap, timeseries_rows=[])
    import re
    # Find the start of the Billable visits card div, then check its class.
    m = re.search(r'(<div class="ss-card[^"]*">[^<]*<span[^>]*>BILLABLE VISITS 30D)',
                  html, re.I)
    assert m, "Billable visits card not found"
    opening_tag = m.group(1)
    assert 'ss-card-ok' not in opening_tag
    assert 'ss-card-watch' not in opening_tag
    assert 'ss-card-bad' not in opening_tag


def test_cert_expiry_card_colours_critical_when_rule_fires():
    site = _wpe_cf_site()
    site["cf"]["cert_expiry"] = {
        "min_days_until_expiry": 5,
        "earliest_expires_on": "2026-05-24",
        "earliest_issuer": "GoogleTrustServices",
        "active_pack_count": 1,
    }
    snap = _snap_with_alerts(site, alerts=[
        _alert(site["key"], "cert_expiry", "critical"),
    ])
    html = render_site_page(site, snap, timeseries_rows=[])
    assert "Cert expiry" in html
    assert _APPLIED_CARD_BAD in html
