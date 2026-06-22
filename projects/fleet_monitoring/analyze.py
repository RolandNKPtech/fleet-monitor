"""Analyze stage — baselines + rule registry + lifecycle + mutes over the latest snapshot."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import date

from .models import SNAPSHOTS_DIR, INTERVENTIONS_FILE
from .interventions import load_interventions, detect_drafts, append_drafts
from .rules import run_all
from .rules.config_drift import load_rule_changes
from .lifecycle import assign_states, load_previous_alerts, save_alerts
from .mutes import apply_mutes, load_mute_entries
from .plan_config import load_plans
from .plan_utilization import evaluate_plan_utilization
from .analytics_health import evaluate_analytics_health
from .timeseries import read_daily_all


def load_snapshots() -> list[dict]:
    """Every snapshot JSON, oldest first."""
    out = []
    for path in sorted(SNAPSHOTS_DIR.glob("*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def build_histories(snapshots: list[dict]) -> dict[str, list[dict]]:
    """key -> [site-entry, ...] across the given snapshots, oldest first."""
    hist: dict[str, list[dict]] = {}
    for snap in snapshots:
        for site in snap.get("sites", []):
            hist.setdefault(site["key"], []).append(site)
    return hist


def analyze_snapshot(current: dict, history_snaps: list[dict],
                     previous_alerts: list, mute_entries: list[dict],
                     current_run_stages: list[dict] | None = None
                     ) -> tuple[dict, list]:
    """Run rules over `current`. Returns (enriched_snapshot, alerts_with_states).

    `history_snaps` are PRIOR snapshots only (current excluded).
    `current_run_stages` is the IN-FLIGHT pipeline stages — passed to
    evaluate_analytics_health so the fleet-level token-failure rule sees
    failures that happened EARLIER in this same run, not just yesterday's
    failures from the disk run-log.
    """
    histories = build_histories(history_snaps)
    rule_changes = load_rule_changes()

    all_alerts = []
    for site in current.get("sites", []):
        site_history = histories.get(site["key"], [])
        all_alerts.extend(run_all(site, site_history, rule_changes=rule_changes))

    # Account-level plan-utilization step — not per-site, runs once per snapshot.
    try:
        today = date.fromisoformat(current.get("date", date.today().isoformat()))
    except ValueError:
        today = date.today()
    all_alerts.extend(evaluate_plan_utilization(
        today, load_plans(), read_daily_all()))

    # Fleet-level: surface any analytics pull source that failed. Reads
    # current_run_stages when available (run.py path) so the rule catches
    # THIS run's failures. Falls back to disk run-log for ad-hoc analyze
    # runs (e.g. `python -m projects.fleet_monitoring.run --analyze`).
    all_alerts.extend(evaluate_analytics_health(
        current_stages=current_run_stages))

    all_alerts = assign_states(all_alerts, previous_alerts)
    all_alerts = apply_mutes(all_alerts, mute_entries)

    # Recompute counts to exclude muted/resolved from the per-site badge.
    active_counts: dict[str, int] = {}
    for a in all_alerts:
        if a.state in ("new", "ongoing"):
            active_counts[a.site_key] = active_counts.get(a.site_key, 0) + 1
    for site in current.get("sites", []):
        site["alerts_count"] = active_counts.get(site["key"], 0)

    current["alerts"] = [a.to_dict() for a in all_alerts]
    return current, all_alerts


def write_drift_drafts(snapshot: dict, path=INTERVENTIONS_FILE) -> int:
    """Append new `us`-attributed config-drift drafts to interventions.yml.

    Returns the number of drafts appended. Failure-isolated — a problem here
    must never abort the analyze stage.
    """
    try:
        existing = load_interventions(path)
        drafts = detect_drafts(snapshot, existing)
        return append_drafts(path, drafts)
    except Exception as e:                            # pragma: no cover
        print(f"  intervention draft detection skipped: {e}", file=sys.stderr)
        return 0


def analyze(current_run_stages: list[dict] | None = None) -> tuple[dict, list]:
    """Load all snapshots, analyze the latest, write results back. Returns (snapshot, alerts).

    `current_run_stages` flows through to evaluate_analytics_health so the
    fleet-level token-failure rule sees this run's failures (chicken-and-
    egg fix — without this, the rule only sees yesterday's failures from
    the on-disk run-log).
    """
    snapshots = load_snapshots()
    if not snapshots:
        raise SystemExit("No snapshots found. Run collect first.")
    current = snapshots[-1]
    history = snapshots[:-1]
    enriched, alerts = analyze_snapshot(current, history, load_previous_alerts(),
                                        load_mute_entries(),
                                        current_run_stages=current_run_stages)
    # Write the enriched snapshot back
    out = SNAPSHOTS_DIR / f"{enriched['date']}.json"
    out.write_text(json.dumps(enriched, indent=2, default=str), encoding="utf-8")
    n_drafts = write_drift_drafts(enriched)
    if n_drafts:
        print(f"  {n_drafts} new intervention draft(s) -> config/interventions.yml")
    save_alerts(alerts)
    new_count = sum(1 for a in alerts if a.state == "new")
    print(f"Analyzed {enriched['date']}: {len(alerts)} alerts, {new_count} new")
    return enriched, alerts


def main():
    argparse.ArgumentParser(description="Fleet monitoring — analyze stage").parse_args()
    try:
        analyze()
    except Exception as e:
        print(f"ANALYZE FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
