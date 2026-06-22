import json
from pathlib import Path
from projects.fleet_monitoring.analyze import analyze_snapshot
from projects.fleet_monitoring.render import render_html

FIX = Path(__file__).parent / "fixtures"


def test_analyze_then_render_end_to_end():
    before = json.loads((FIX / "snapshot-before.json").read_text())
    after = json.loads((FIX / "snapshot-after.json").read_text())
    # 7 copies of `before` gives spiker.com a baseline of 45 → 120 is a clear spike
    history = [before] * 7

    enriched, alerts = analyze_snapshot(after, history, previous_alerts=[], mute_entries=[])

    rules_fired = {a.rule for a in alerts}
    assert "bandwidth_spike" in rules_fired      # spiker.com 120 vs ~45 baseline
    assert "config_drift" in rules_fired         # spiker.com ssl strict→full
    assert "mb_per_visit_high" in rules_fired    # spiker.com 57 MB/visit
    # calm.com is steady — it should not spike
    assert not any(a.site_key == "calm.com" and a.rule == "bandwidth_spike" for a in alerts)

    html = render_html(enriched, timeseries_rows=[])
    assert "<!DOCTYPE html>" in html
    assert "spiker.com" in html
