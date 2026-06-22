"""Fixture-driven end-to-end smoke test for the plan-utilization pipeline.

Exercises: fixture plan YAML + fixture daily.jsonl + fixture snapshot
→ analyze_snapshot → render_html → dashboard HTML on disk.
No network calls.
"""
import json
from datetime import date
from pathlib import Path

from projects.fleet_monitoring.analyze import analyze_snapshot
from projects.fleet_monitoring.render import render_html


def test_smoke_plan_utilization_end_to_end(tmp_path, monkeypatch):
    """Fixture plans + fixture daily.jsonl flow into a fully rendered dashboard."""
    # Seed the plan config file
    import projects.fleet_monitoring.plan_config as pc
    plans_file = tmp_path / "wpe-plans.yml"
    plans_file.write_text(
        "accounts:\n"
        "  acctF: {cycle_start_day: 13, bandwidth_gb_limit: 1700, "
        "visits_limit: null, overage_per_gb_usd: 0.30}\n"
        "  acctD: {cycle_start_day: null, bandwidth_gb_limit: null, "
        "visits_limit: null, overage_per_gb_usd: null}\n",
        encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", plans_file)

    # Seed daily.jsonl with 7 days of cycle data for acctF
    import projects.fleet_monitoring.timeseries as ts
    daily_file = tmp_path / "daily.jsonl"
    daily_lines = []
    for d in range(13, 20):                                  # 7 days
        daily_lines.append(json.dumps({
            "date": f"2026-05-{d:02d}", "account": "acctF",
            "install": "myliposuctprod", "account_id": "uuid-6",
            "network_total_bytes": 55_000_000_000,            # 55 GB/day → projects to ~100.3% over 31-day cycle
            "billable_visits": 800,
        }))
    daily_file.write_text("\n".join(daily_lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(ts, "DAILY_FILE", daily_file)

    snapshot = {
        "schema_version": 1, "date": "2026-05-19",
        "captured_at": "2026-05-19T06:08:00+00:00",
        "run": {"duration_s": 800,
                "coverage": {"wpe": "1/1", "cf_config": "1/1"}},
        "roster_summary": {"total": 2, "wpe+cf": 1, "wpe-only": 1, "cf-only": 0},
        "sites": [
            {"key": "myliposuction.com", "join_state": "wpe+cf",
             "wpe": {"account_name": "acctF", "account": "uuid-6",
                     "install": "myliposuctprod",
                     "bandwidth_gb_30d": 1200, "billable_visits_30d": 30000,
                     "mb_per_visit": 40},
             "cf": {"analytics": {"cache_hit_rate": 70.0, "threats": 12}},
             "alerts_count": 0},
            {"key": "calm.com", "join_state": "wpe-only",
             "wpe": {"account_name": "acctD", "account": "uuid-4",
                     "install": "calmprd",
                     "bandwidth_gb_30d": 956, "billable_visits_30d": 5000,
                     "mb_per_visit": 13},
             "cf": None, "probe": None, "overlay": None,
             "alerts_count": 0},
        ],
    }

    enriched, alerts = analyze_snapshot(
        snapshot, [], previous_alerts=[], mute_entries=[])

    # acctF: 7 × 55 = 385 GB used, projected 385/7 × 31 = ~1705 GB
    # 1705 / 1700 = ~100.3% projected → projection alert (cycle-to-date is 22.6%)
    plan_alerts = [a for a in alerts if a.rule == "plan_utilization"]
    assert any(a.site_key == "acctF" for a in plan_alerts)
    nm6 = next(a for a in plan_alerts if a.site_key == "acctF")
    assert nm6.detail["kind"] in {"threshold", "projection"}

    # Render the full HTML and assert the panel content is present
    html = render_html(enriched, timeseries_rows=[])
    out = tmp_path / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    assert out.stat().st_size > 5000

    text = out.read_text(encoding="utf-8")
    assert "Plan Utilization" in text
    assert "acctF" in text                  # configured account
    assert "of 1,700 GB" in text                  # limit value rendered
    assert "acctD" in text                  # unconfigured account too
    assert "plan limit not set" in text           # placeholder CTA
    assert "956" in text                          # rolling 30d for nm4
