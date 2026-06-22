"""Fleet-level analytics health evaluator.

Watches the run-log for failed GA4/GSC pulls. Token expiry is silent and
compounding — every nightly run after the token dies skips that source,
the dashboard keeps showing whatever was last in the lake, and the
operator never notices until a stakeholder asks "why hasn't this site
had new data in two weeks."

This evaluator reads the most recent run-log entry, finds any
`stages.sub_steps` with `ok=false`, and emits one critical alert per
failed source. site_key is "fleet" to mark it as a fleet-level signal —
no per-site repetition.

NOT a per-site rule, so not in rules.REGISTRY. analyze.py calls
`evaluate_analytics_health` once per snapshot, like
evaluate_plan_utilization.
"""
from __future__ import annotations
import json
from pathlib import Path

from .models import Alert, SEVERITY_CRITICAL, RUN_LOG_FILE


RULE_ID = "analytics_token_failure"
FLEET_SITE_KEY = "fleet"


def _latest_entry(path: Path = RUN_LOG_FILE) -> dict | None:
    """Last line of the run-log as a dict, or None if file missing / malformed."""
    if not path.exists():
        return None
    last = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def evaluate_analytics_health(current_stages: list[dict] | None = None,
                              path: Path = RUN_LOG_FILE) -> list[Alert]:
    """Return one critical Alert per failed analytics sub-source.

    `current_stages` is the IN-FLIGHT stages list from run.py (the same
    list the contextmanager is mutating). When provided, we use it
    directly — this is the correct path because the rule runs DURING
    analyze, before the run-log entry for this run gets written in the
    finally: block of main(). Without it, the rule would only ever see
    yesterday's failures, never today's. Chicken-and-egg fix.

    Falls back to reading the most recent run-log entry on disk when
    no in-flight stages are supplied — keeps tests + future callers
    working without coordinating with run.py."""
    if current_stages is not None:
        stages = current_stages
        run_date = None
        run_logged_at = None
    else:
        entry = _latest_entry(path)
        if not entry:
            return []
        stages = entry.get("stages") or []
        run_date = entry.get("date")
        run_logged_at = entry.get("logged_at")

    alerts: list[Alert] = []
    for stage in stages:
        for sub in (stage.get("sub_steps") or []):
            if sub.get("ok", True):
                continue
            source = sub.get("name", "unknown")
            err = (sub.get("error") or "no error captured")[-200:]
            short_source = source.split(".")[-1] if "." in source else source
            alerts.append(Alert(
                site_key=FLEET_SITE_KEY,
                rule=RULE_ID,
                severity=SEVERITY_CRITICAL,
                summary=(f"{source} failed in last pipeline run — data lake "
                         f"is stale for this source. Likely cause: expired "
                         f"token. Re-mint via scripts/ then trigger a Refresh."),
                detail={
                    "source": source,
                    "short_source": short_source,
                    "error": err,
                    "run_date": run_date,
                    "run_logged_at": run_logged_at,
                },
                dedup_key=source,  # one alert per source, stable across runs
            ))
    return alerts
