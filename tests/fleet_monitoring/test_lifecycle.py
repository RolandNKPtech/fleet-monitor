import json

from projects.fleet_monitoring.models import Alert, SEVERITY_WARNING
from projects.fleet_monitoring.lifecycle import (
    assign_states, save_alerts, load_previous_alerts)


def _alert(site, rule, detail):
    return Alert(site, rule, SEVERITY_WARNING, "x", detail)


def test_assign_states_marks_new_ongoing_resolved():
    prev = [
        _alert("a.com", "bandwidth_spike", {"v": 1}),    # will persist -> ongoing
        _alert("b.com", "bot_ratio", {"v": 2}),          # gone this run -> resolved
    ]
    current = [
        _alert("a.com", "bandwidth_spike", {"v": 1}),    # same fp -> ongoing
        _alert("c.com", "probe_failure", {"v": 3}),      # brand new -> new
    ]
    result = assign_states(current, prev)
    by_site = {a.site_key: a for a in result}
    assert by_site["a.com"].state == "ongoing"
    assert by_site["c.com"].state == "new"
    assert by_site["b.com"].state == "resolved"           # carried in from prev
    assert by_site["b.com"] not in current                # resolved alerts appended


def test_save_alerts_drops_resolved_so_they_do_not_pile_up(
        tmp_path, monkeypatch):
    """Without this, resolved alerts get re-loaded as previous, re-marked
    resolved every run, and accumulate forever — the 368-of-647 stale-state
    bug."""
    from projects.fleet_monitoring import lifecycle as lc, models
    alerts_file = tmp_path / "alerts-latest.json"
    monkeypatch.setattr(models, "ALERTS_LATEST_FILE", alerts_file)
    monkeypatch.setattr(lc, "ALERTS_LATEST_FILE", alerts_file)

    a = _alert("a.com", "bot_ratio", {"v": 1}); a.state = "ongoing"
    b = _alert("b.com", "bot_ratio", {"v": 2}); b.state = "new"
    r = _alert("c.com", "bot_ratio", {"v": 3}); r.state = "resolved"
    m = _alert("d.com", "bot_ratio", {"v": 4}); m.state = "muted"

    save_alerts([a, b, r, m])
    saved = json.loads(alerts_file.read_text(encoding="utf-8"))
    keys = sorted(d["site_key"] for d in saved)
    assert keys == ["a.com", "b.com", "d.com"]   # ongoing + new + muted
    assert "c.com" not in keys                    # resolved dropped
