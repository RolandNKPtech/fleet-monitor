# tests/fleet_monitoring/test_render.py
import re

from projects.fleet_monitoring.render import (
    render_html, _bandwidth_chart, _per_account_lines_chart)


def _snapshot():
    return {
        "date": "2026-05-16",
        "captured_at": "2026-05-16T06:04:00+00:00",
        "run": {"duration_s": 840, "coverage": {"wpe": "243/248", "cf_config": "266/268"}},
        "roster_summary": {"total": 247, "wpe+cf": 200, "wpe-only": 30, "cf-only": 17},
        "sites": [
            {"key": "example-clinic.com", "join_state": "wpe+cf",
             "wpe": {"account": "a1", "bandwidth_gb_30d": 113.8, "mb_per_visit": 34.4,
                     "billable_visits_30d": 3313},
             "cf": {"analytics": {"cache_hit_rate": 78.0, "threats": 12}},
             "alerts_count": 0},
        ],
        "alerts": [
            {"site_key": "example-doc-a.com", "rule": "config_drift", "severity": "critical",
             "summary": "ssl: strict -> full", "detail": {"kind": "ssl_downgrade",
             "attribution": "external"},
             "state": "new", "mute_reason": None, "fingerprint": "x:y:z"},
        ],
    }


def test_render_html_includes_tabs_and_alert_and_site():
    html = render_html(_snapshot(), timeseries_rows=[])
    assert "<!DOCTYPE html>" in html
    for tab in ("Overview", "Sites", "Trends", "Changelog"):
        assert tab in html
    assert "example-doc-a.com" in html        # the NEW alert shows on Overview
    assert "example-clinic.com" in html              # the site shows in the Sites table
    assert "243/248" in html                # coverage indicator in the header


def test_render_html_has_summary_stat_cards():
    html = render_html(_snapshot(), timeseries_rows=[])
    assert "stat-card" in html              # the KPI cards row
    assert "Total fleet bandwidth" in html
    assert "New alerts" in html


def test_render_html_includes_cf_cost_summary_when_plans_present():
    snap = _snapshot()
    snap["sites"][0]["cf"]["plan"] = {
        "name": "Pro Website", "price": 20, "frequency": "monthly",
        "currency": "USD",
    }
    snap["sites"].append({
        "key": "free.com", "join_state": "cf-only",
        "cf": {"plan": {"name": "Free Website", "price": 0,
                        "frequency": "monthly", "currency": "USD"}},
    })
    html = render_html(snap, timeseries_rows=[])
    assert "CF subscription cost" in html
    assert "Pro Website" in html
    assert "Free Website" in html
    assert "projection from CF plan prices" in html   # honesty disclaimer


def test_render_html_cost_summary_empty_state_when_no_plans():
    # Snapshot's lone site has cf but no plan -> empty state, not crash.
    html = render_html(_snapshot(), timeseries_rows=[])
    assert "CF subscription cost" in html
    assert "No CF plan info available" in html


def test_render_html_empty_state_when_no_new_alerts():
    snap = _snapshot()
    snap["alerts"] = []
    html = render_html(snap, timeseries_rows=[])
    assert "All clear" in html


def test_render_html_is_self_contained():
    # no external CSS/JS references — the file must open anywhere
    html = render_html(_snapshot(), timeseries_rows=[])
    assert "<link" not in html
    assert "<script src" not in html


def test_period_delta_compares_30d_rolling_totals_at_two_dates():
    from projects.fleet_monitoring.render import _period_delta, _fleet_total_by_date
    rows = [
        # site a: 30d rolling = 100 GB on day 1, 80 GB on day 16 (improved 20 GB)
        {"date": "2026-01-01", "key": "a.com", "bandwidth_gb": 100.0},
        {"date": "2026-01-16", "key": "a.com", "bandwidth_gb": 80.0},
        # site b: 30d rolling = 50 GB on day 1, 55 GB on day 16 (regressed 5 GB)
        {"date": "2026-01-01", "key": "b.com", "bandwidth_gb": 50.0},
        {"date": "2026-01-16", "key": "b.com", "bandwidth_gb": 55.0},
    ]
    by_date = _fleet_total_by_date(rows)
    d = _period_delta(by_date, period_days=15)
    assert d["current_date"] == "2026-01-16"
    assert d["prev_date"] == "2026-01-01"
    assert d["current_gb"] == 135.0 and d["prev_gb"] == 150.0
    assert d["delta_gb"] == -15.0       # net improvement
    assert d["delta_pct"] == -10.0


def test_period_delta_returns_none_when_not_enough_history():
    from projects.fleet_monitoring.render import _period_delta, _fleet_total_by_date
    rows = [{"date": "2026-01-01", "key": "a.com", "bandwidth_gb": 100.0}]
    assert _period_delta(_fleet_total_by_date(rows), period_days=15) is None


def test_render_html_includes_plan_utilization_panel():
    """The full Overview tab now carries the Plan Utilization heading."""
    html = render_html(_snapshot(), timeseries_rows=[])
    assert "Plan Utilization" in html


def test_per_account_by_date_groups_bandwidth_per_account_per_day():
    from projects.fleet_monitoring.render import _per_account_by_date
    rows = [
        {"date": "2026-05-15", "key": "a.com", "account": "acctA", "bandwidth_gb": 10.0},
        {"date": "2026-05-15", "key": "b.com", "account": "acctA", "bandwidth_gb": 5.0},
        {"date": "2026-05-15", "key": "c.com", "account": "acctB", "bandwidth_gb": 20.0},
        {"date": "2026-05-16", "key": "a.com", "account": "acctA", "bandwidth_gb": 12.0},
        {"date": "2026-05-16", "key": "c.com", "account": "acctB", "bandwidth_gb": 25.0},
        # missing account → grouped under "(unassigned)" so operator sees the gap
        {"date": "2026-05-16", "key": "orphan.com", "account": None, "bandwidth_gb": 3.0},
    ]
    out = _per_account_by_date(rows)
    assert out["acctA"] == {"2026-05-15": 15.0, "2026-05-16": 12.0}
    assert out["acctB"] == {"2026-05-15": 20.0, "2026-05-16": 25.0}
    assert out["(unassigned)"] == {"2026-05-16": 3.0}


def test_per_account_lines_chart_renders_svg_with_one_path_per_account():
    from projects.fleet_monitoring.render import _per_account_lines_chart
    per_acct = {
        "acctA": {"2026-05-15": 15.0, "2026-05-16": 12.0, "2026-05-17": 18.0},
        "acctB": {"2026-05-15": 20.0, "2026-05-16": 25.0, "2026-05-17": 22.0},
    }
    html = _per_account_lines_chart(per_acct)
    assert "<svg" in html
    assert html.count("class=\"pa-line\"") == 2     # one path per account
    assert "acctA" in html                       # legend label
    assert "acctB" in html
    assert "Per-account" in html or "per-account" in html.lower()


def test_per_account_lines_chart_empty_state_when_one_snapshot():
    from projects.fleet_monitoring.render import _per_account_lines_chart
    per_acct = {"acctA": {"2026-05-15": 15.0}}
    html = _per_account_lines_chart(per_acct)
    # One data point can't form a line — show explanatory empty state, no SVG.
    assert "<svg" not in html
    assert "snapshot" in html.lower() or "history" in html.lower()


def test_per_account_lines_chart_empty_state_when_no_data():
    from projects.fleet_monitoring.render import _per_account_lines_chart
    html = _per_account_lines_chart({})
    assert "<svg" not in html


def test_trends_tab_includes_per_account_chart_above_fleet_chart():
    """Per-account chart appears BEFORE the fleet bar chart in the Trends tab."""
    rows = [
        {"date": "2026-05-15", "key": "a.com", "account": "acctA", "bandwidth_gb": 10.0},
        {"date": "2026-05-16", "key": "a.com", "account": "acctA", "bandwidth_gb": 12.0},
        {"date": "2026-05-15", "key": "c.com", "account": "acctB", "bandwidth_gb": 20.0},
        {"date": "2026-05-16", "key": "c.com", "account": "acctB", "bandwidth_gb": 25.0},
    ]
    snap = _snapshot()
    html = render_html(snap, timeseries_rows=rows)
    # "Fleet bandwidth over time" appears in BOTH Overview and Trends; rfind
    # gets the Trends one, which is what we're sequencing against.
    pa_idx = html.rfind("Per-account bandwidth")
    fleet_idx = html.rfind("Fleet bandwidth over time")
    assert pa_idx >= 0 and fleet_idx >= 0, "both charts must render"
    assert pa_idx < fleet_idx, "per-account chart should precede fleet chart"


def test_sites_table_key_cell_links_to_site_page():
    snap = _snapshot()
    html = render_html(snap, timeseries_rows=[])
    # The site-key cell wraps the key in <a href="sites/<safe>.html">
    assert 'href="sites/example-clinic.com.html"' in html
    assert 'class="site-link"' in html


def test_render_writes_one_site_page_per_site(tmp_path, monkeypatch):
    """End-to-end: render() (not just render_html) writes site files."""
    import projects.fleet_monitoring.render as rmod
    import projects.fleet_monitoring.render_site as rs
    # Point both module-level Path constants at the tmp dir
    snap_dir = tmp_path / "snaps"; snap_dir.mkdir()
    sites_dir = tmp_path / "sites"
    dash_file = tmp_path / "dashboard.html"
    monkeypatch.setattr(rmod, "SNAPSHOTS_DIR", snap_dir)
    monkeypatch.setattr(rmod, "DASHBOARD_FILE", dash_file)
    monkeypatch.setattr(rs, "SITES_DIR", sites_dir)
    # Write a minimal analyzed snapshot
    import json
    (snap_dir / "2026-05-19.json").write_text(json.dumps(_snapshot()), encoding="utf-8")
    rmod.render()
    assert dash_file.exists()
    files = list(sites_dir.glob("*.html"))
    assert len(files) >= 1


def test_latest_storage_gb_returns_latest_day_total():
    from projects.fleet_monitoring.render import _latest_storage_gb
    wpe = {"daily": [
        {"date": "2026-05-17", "storage_file_bytes": 1_000_000_000,
         "storage_database_bytes": 200_000_000},
        {"date": "2026-05-19", "storage_file_bytes": 4_000_000_000,
         "storage_database_bytes": 500_000_000},
        {"date": "2026-05-18", "storage_file_bytes": 2_000_000_000,
         "storage_database_bytes": 300_000_000},
    ]}
    # latest date is 2026-05-19 -> (4.0 + 0.5) GB
    assert _latest_storage_gb(wpe) == 4.5


def test_latest_storage_gb_none_when_no_daily():
    from projects.fleet_monitoring.render import _latest_storage_gb
    assert _latest_storage_gb({}) is None
    assert _latest_storage_gb({"daily": []}) is None
    assert _latest_storage_gb({"daily": [{"storage_file_bytes": 1}]}) is None  # no date


def test_latest_storage_gb_treats_missing_field_as_zero():
    from projects.fleet_monitoring.render import _latest_storage_gb
    wpe = {"daily": [{"date": "2026-05-19", "storage_file_bytes": 3_000_000_000}]}
    # storage_database_bytes absent -> 0; total = 3.0 GB
    assert _latest_storage_gb(wpe) == 3.0


def test_sites_table_has_storage_column_header():
    html = render_html(_snapshot(), timeseries_rows=[])
    assert '<th class="num">Storage GB</th>' in html


def test_sites_table_storage_cell_shows_gb_value():
    snap = _snapshot()
    snap["sites"][0]["wpe"]["daily"] = [
        {"date": "2026-05-16", "storage_file_bytes": 6_200_000_000,
         "storage_database_bytes": 800_000_000},
    ]
    html = render_html(snap, timeseries_rows=[])
    # (6.2 + 0.8) GB, 1 decimal — assert the exact table cell to avoid a
    # coincidental substring match against another number on the page.
    assert '<td class="num">7.0</td>' in html


def test_render_html_includes_interventions_tab():
    html = render_html(_snapshot(), timeseries_rows=[])
    assert "showTab(4,this)" in html
    assert "Interventions" in html


def test_render_html_interventions_view_passed_through():
    view = {"needs_review": 2, "rows": []}
    html = render_html(_snapshot(), timeseries_rows=[], interventions_view=view)
    assert "awaiting review" in html


def test_render_writes_console_html(tmp_path, monkeypatch):
    """render() emits console.html alongside dashboard.html."""
    import json
    import projects.fleet_monitoring.render as rmod
    import projects.fleet_monitoring.render_site as rs
    snap_dir = tmp_path / "snaps"; snap_dir.mkdir()
    monkeypatch.setattr(rmod, "SNAPSHOTS_DIR", snap_dir)
    monkeypatch.setattr(rmod, "DASHBOARD_FILE", tmp_path / "dashboard.html")
    monkeypatch.setattr(rmod, "CONSOLE_FILE", tmp_path / "console.html")
    monkeypatch.setattr(rs, "SITES_DIR", tmp_path / "sites")
    (snap_dir / "2026-05-19.json").write_text(json.dumps(_snapshot()), encoding="utf-8")
    rmod.render()
    console = tmp_path / "console.html"
    assert console.exists()
    text = console.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert "Fleet Console" in text


def test_dashboard_js_has_hash_tab_deeplink():
    """dashboard.html honours a #tab hash on load."""
    html = render_html(_snapshot(), timeseries_rows=[])
    assert "location.hash" in html
    assert "applyHashTab" in html


# --- Fleet bandwidth line chart -------------------------------------------

def _bw_rows():
    return [
        {"key": "a.com", "date": "2026-05-19", "bandwidth_gb": 100.0},
        {"key": "b.com", "date": "2026-05-19", "bandwidth_gb": 50.0},
        {"key": "a.com", "date": "2026-05-20", "bandwidth_gb": 130.0},
        {"key": "a.com", "date": "2026-05-21", "bandwidth_gb": 90.0},
    ]


def test_bandwidth_chart_renders_a_line_not_bars():
    html = _bandwidth_chart(_bw_rows())
    assert '<polyline class="lchart-line"' in html
    assert "bchart-bar" not in html  # the old bar chart is gone


def test_bandwidth_chart_has_one_dot_per_snapshot_date():
    html = _bandwidth_chart(_bw_rows())
    # three distinct dates -> three visible data dots
    assert html.count('class="lchart-dot') == 3


def test_bandwidth_chart_highlights_the_latest_point():
    html = _bandwidth_chart(_bw_rows())
    assert "lchart-dot is-latest" in html


def test_bandwidth_chart_has_honest_gridline_labels():
    html = _bandwidth_chart(_bw_rows())
    assert "lchart-grid" in html
    assert 'class="lchart-ylab"' in html


def test_bandwidth_chart_point_has_hover_title():
    html = _bandwidth_chart(_bw_rows())
    assert "<title>" in html
    assert "GB" in html


def test_bandwidth_chart_single_snapshot_shows_message_not_line():
    html = _bandwidth_chart(
        [{"key": "a.com", "date": "2026-05-21", "bandwidth_gb": 100.0}])
    assert "lchart-line" not in html
    assert "snapshot" in html.lower()


def test_bandwidth_chart_empty_state():
    html = _bandwidth_chart([])
    assert "No history yet" in html


# --- Per-account bandwidth chart ------------------------------------------

def _pa():
    return {
        "acct-a": {"2026-05-19": 2000.0, "2026-05-20": 2050.0,
                   "2026-05-21": 1980.0},
        "acct-b": {"2026-05-19": 1000.0, "2026-05-20": 1010.0,
                   "2026-05-21": 990.0},
    }


def test_per_account_chart_y_axis_is_auto_scaled():
    html = _per_account_lines_chart(_pa())
    labels = re.findall(r'class="pa-y"[^>]*>([\d,]+)</text>', html)
    vals = sorted(int(s.replace(",", "")) for s in labels)
    assert vals, "no y-axis labels found"
    # bottom gridline sits near the data minimum (990), not at 0
    assert 0 < vals[0] < 990
    # top gridline is at or above the data maximum (2050)
    assert vals[-1] >= 2050


def test_per_account_chart_draws_one_line_per_account():
    html = _per_account_lines_chart(_pa())
    assert html.count('class="pa-line"') == 2


def test_per_account_chart_empty_state():
    html = _per_account_lines_chart({})
    assert "No history yet" in html
