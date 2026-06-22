"""Smoke test — full pipeline end-to-end using Task 20 fixtures, no live API calls."""
import json
from pathlib import Path
from projects.fleet_monitoring.analyze import analyze_snapshot
from projects.fleet_monitoring.render import render_html

FIX = Path(__file__).parent / "fixtures"


def test_full_pipeline_smoke_produces_openable_dashboard(tmp_path):
    before = json.loads((FIX / "snapshot-before.json").read_text())
    after = json.loads((FIX / "snapshot-after.json").read_text())
    enriched, alerts = analyze_snapshot(after, [before] * 7,
                                        previous_alerts=[], mute_entries=[])
    html = render_html(enriched, timeseries_rows=[
        {"date": "2026-05-08", "key": "calm.com", "bandwidth_gb": 40.0},
        {"date": "2026-05-16", "key": "calm.com", "bandwidth_gb": 41.0},
    ])
    out = tmp_path / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    assert out.exists()
    assert out.stat().st_size > 2000                  # real content, not a stub
    text = out.read_text(encoding="utf-8")
    for marker in ("Needs Attention", "Fleet Rollup", "stat-card", "spiker.com"):
        assert marker in text
    assert "bandwidth over time" in text               # trends rendered from rows
    # the analyze pipeline produced alerts and they reached the dashboard
    assert enriched["sites"]                            # snapshot survived analyze
    assert any(a.rule == "bandwidth_spike" for a in alerts)
