"""Alert lifecycle — diff this run's alerts against the last run's.

new       : fingerprint absent from the previous run
ongoing   : fingerprint present in both runs
resolved  : fingerprint present in the previous run, absent now (appended to output)
muted alerts keep their 'muted' state — apply_mutes runs after this.
"""
from __future__ import annotations
import json

from .models import Alert, ALERTS_LATEST_FILE


def assign_states(current: list[Alert], previous: list[Alert]) -> list[Alert]:
    """Return current alerts with states assigned, plus resolved carry-ins."""
    prev_fps = {a.fingerprint() for a in previous}
    cur_fps = {a.fingerprint() for a in current}

    for alert in current:
        alert.state = "ongoing" if alert.fingerprint() in prev_fps else "new"

    resolved = []
    for alert in previous:
        if alert.fingerprint() not in cur_fps:
            alert.state = "resolved"
            resolved.append(alert)

    return current + resolved


def load_previous_alerts() -> list[Alert]:
    """Read data/alerts-latest.json. Returns [] if absent."""
    if not ALERTS_LATEST_FILE.exists():
        return []
    raw = json.loads(ALERTS_LATEST_FILE.read_text(encoding="utf-8"))
    return [Alert.from_dict(d) for d in raw]


def save_alerts(alerts: list[Alert]) -> None:
    """Persist the active (non-resolved) alerts for next run's lifecycle diff.

    Resolved carry-ins must NOT be saved — otherwise they get re-loaded as
    `previous`, re-marked resolved every run, and accumulate forever (the
    bug that put 368 stale alerts in a 647-alert snapshot)."""
    ALERTS_LATEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    keep = [a for a in alerts if a.state != "resolved"]
    ALERTS_LATEST_FILE.write_text(
        json.dumps([a.to_dict() for a in keep], indent=2), encoding="utf-8")
