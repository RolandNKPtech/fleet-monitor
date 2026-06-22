"""Tests for the R2 Health dashboard tab.

The data source (monitor_r2_health.py) ships per-site rows shaped as::

    {"apex": "site.com", "install": "siteprd", "source": "tracker",
     "status": "ok", "probed": 5, "broken_count": 1, "broken_ids": ["123"]}

The renderer derives an operator-facing status label and an empty-state, then
emits an HTML table. These tests pin the classifier (which is what makes
ROWS turn red/yellow/green) and confirm the renderer survives degenerate
inputs (empty results, scanner failure, missing file).
"""
from projects.fleet_monitoring.render import (
    _r2_health_status, _r2_health_tab, _r2_health_load)


def test_status_classifier_needs_resync_when_broken_present():
    assert _r2_health_status({
        "status": "ok", "probed": 5, "broken_count": 1, "broken_ids": ["x"]
    }) == "needs_resync"


def test_status_classifier_clean_when_probed_and_zero_broken():
    assert _r2_health_status({
        "status": "ok", "probed": 5, "broken_count": 0
    }) == "clean"


def test_status_classifier_no_recent_upload_when_nothing_probed():
    assert _r2_health_status({
        "status": "ok", "probed": 0, "broken_count": 0
    }) == "no_recent_upload"


def test_status_classifier_scan_failed_when_scanner_errored():
    assert _r2_health_status({
        "status": "error", "error": "ssh timeout"
    }) == "scan_failed"


def test_status_classifier_broken_wins_over_no_probes():
    # Defensive: if the scanner reports broken_count>0 with probed==0
    # (shouldn't happen, but...) the action signal must win.
    assert _r2_health_status({
        "status": "ok", "probed": 0, "broken_count": 3
    }) == "needs_resync"


def test_render_tab_empty_state_when_no_payload():
    """Missing latest.json (first-run / not yet pushed) must render guidance,
    not raise — operator opening the dashboard before the first scan should
    see actionable next-step text."""
    html = _r2_health_tab(None)
    assert "R2 Health" in html
    assert "monitor_r2_health.py" in html
    assert "needs_resync" not in html


def test_render_tab_with_real_shaped_payload():
    """One needs-resync row + one clean row should render with both statuses,
    show the broken-IDs cell, and put the actionable site on top via sort."""
    payload = {
        "date": "2026-06-23",
        "days_window": 30,
        "results": [
            {"apex": "siteA.com", "install": "siteA", "source": "tracker",
             "status": "ok", "probed": 10, "broken_count": 0, "broken_ids": []},
            {"apex": "siteB.com", "install": "siteB", "source": "tracker",
             "status": "ok", "probed": 5, "broken_count": 2,
             "broken_ids": ["111", "222"]},
        ],
        "totals": {"sites_scanned": 2, "total_probed": 15, "total_broken": 2,
                   "sites_with_broken": 1},
    }
    html = _r2_health_tab(payload)
    assert "siteA.com" in html
    assert "siteB.com" in html
    assert "111" in html and "222" in html
    assert "1 sites need resync" in html
    assert "2026-06-23" in html and "last 30 days" in html
    # needs_resync site must render BEFORE the clean site (sort prioritises
    # actionable rows so the operator scans top-down).
    assert html.index("siteB.com") < html.index("siteA.com")


def test_render_tab_truncates_long_broken_id_list():
    """A site with >5 broken IDs shows the first 5 + a +N overflow marker so
    a runaway scan doesn't blow up the table cell."""
    payload = {
        "date": "2026-06-23", "days_window": 30,
        "results": [{
            "apex": "x.com", "install": "x", "source": "tracker",
            "status": "ok", "probed": 50, "broken_count": 8,
            "broken_ids": ["1", "2", "3", "4", "5", "6", "7", "8"],
        }],
        "totals": {"sites_scanned": 1, "total_probed": 50, "total_broken": 8,
                   "sites_with_broken": 1},
    }
    html = _r2_health_tab(payload)
    assert "1, 2, 3, 4, 5" in html
    assert "+3" in html


def test_loader_returns_none_when_file_missing(tmp_path, monkeypatch):
    """Loader must return None (not raise) when the scan JSON hasn't been
    pulled yet — the tab handles None by rendering the empty state."""
    from projects.fleet_monitoring import models
    monkeypatch.setattr(models, "ROOT", tmp_path)
    assert _r2_health_load() is None


def test_loader_returns_none_on_corrupt_json(tmp_path, monkeypatch):
    """Loader must NOT raise on a half-written JSON file — local cron could
    crash mid-write. None means "no data"; the dashboard handles that."""
    from projects.fleet_monitoring import models
    monkeypatch.setattr(models, "ROOT", tmp_path)
    out = tmp_path / "data" / "reports" / "r2-health"
    out.mkdir(parents=True)
    (out / "latest.json").write_text("{not valid json", encoding="utf-8")
    assert _r2_health_load() is None
