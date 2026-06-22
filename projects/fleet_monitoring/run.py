"""Orchestrator — chain collect → analyze → render, append timeseries, log the run.

Usage:
    python projects/fleet_monitoring/run.py                 # all three stages
    python projects/fleet_monitoring/run.py --collect       # one or more stages
    python projects/fleet_monitoring/run.py --analyze --render
    python projects/fleet_monitoring/run.py --no-probes     # collect without probes
"""
from __future__ import annotations

# Script-mode bootstrap: when invoked as `python projects/fleet_monitoring/run.py`
# (not as `-m`), __package__ is empty and relative imports below would fail.
# Adding the repo root to sys.path + naming the package makes both invocations work.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    __package__ = "projects.fleet_monitoring"

# Load .env BEFORE the relative imports — wpe_api.py reads WPE_API_USER /
# WPE_API_PASSWORD at module-import time, so the env must be populated first.
from pathlib import Path as _RootPath
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(_RootPath(__file__).resolve().parents[2] / ".env")

import argparse
import asyncio
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import date as _date
from datetime import datetime, timezone

from .models import RUN_LOG_FILE, FLEET_DB
from . import collect as collect_mod
from . import analyze as analyze_mod
from . import render as render_mod
from . import fleet_db as fleet_db_mod
from . import effectiveness as effectiveness_mod
from . import r2_state as r2_state_mod
from .timeseries import rollup_rows, append_rollup, daily_rollup_rows, append_daily, read_daily_all
from .interventions import load_interventions
from .analyze import load_snapshots


def headline(snapshot: dict, alerts: list[dict]) -> str:
    """One-line terminal summary printed after a run."""
    total = snapshot.get("roster_summary", {}).get("total", "?")
    new = [a for a in alerts if a.get("state") == "new"]
    crits = [a for a in new if a.get("severity") == "critical"]
    base = f"Fleet monitor: {total} sites · {len(new)} NEW"
    if crits:
        c = crits[0]
        more = f" +{len(crits)-1} more" if len(crits) > 1 else ""
        return f"{base} ({len(crits)} critical: {c['site_key']} {c['rule']}{more})"
    if not new:
        return f"{base} — all clear"
    return base


def run_log_entry(date_str: str, duration_s: int, coverage: dict,
                  alert_counts: dict, stages: list[dict] | None = None,
                  status: str = "ok", error: str | None = None) -> dict:
    """One per-run record for data/run-log.jsonl.

    `stages`: list of {name, duration_s, ok} entries — surfaces per-stage
              timing on the /pipeline page so an operator can see drift
              like "GA4 pull went 30s -> 240s = token throttle".
    `status`: 'ok' if all stages completed; 'failed' if the run raised.
              `error` carries the exception summary when status='failed'.
    """
    entry = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "date": date_str,
        "duration_s": duration_s,
        "coverage": coverage,
        "alert_counts": alert_counts,
        "stages": stages or [],
        "status": status,
    }
    if error:
        entry["error"] = error
    return entry


@contextmanager
def _stage(name: str, stages: list[dict], sub_steps: list[dict] | None = None):
    """Record a stage's wall-clock + ok flag. Re-raises any exception so the
    outer run-log writer can mark the run as failed.

    Pass `sub_steps` (a list the wrapped block mutates / extends) to capture
    per-sub-step status. When provided, the stage's `ok` reflects the AND of
    every sub-step's ok — so a single failed source flips the stage to
    failed even if the subprocess itself returned 0. The sub_steps list is
    attached to the stage entry under "sub_steps".

    Usage:
        stages: list[dict] = []
        with _stage('collect', stages):
            ...do work...

        sub: list[dict] = []
        with _stage('analytics_pull', stages, sub_steps=sub):
            sub.extend(_pull_analytics_lake())
    """
    started = time.monotonic()
    ok = True
    try:
        yield
    except Exception:
        ok = False
        raise
    finally:
        entry: dict = {
            "name": name,
            "duration_s": round(time.monotonic() - started, 1),
            "ok": ok,
        }
        if sub_steps is not None:
            entry["sub_steps"] = list(sub_steps)
            if ok:
                entry["ok"] = all(s.get("ok", True) for s in sub_steps)
        stages.append(entry)


def _append_run_log(entry: dict) -> None:
    RUN_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _pull_analytics_lake() -> list[dict]:
    """Pull fresh GA4 + GSC data into the local lake before collect runs.

    Each step is a subprocess so a token / network failure in one pull does not
    poison the others or this process. Errors degrade to a stderr line — the
    fleet pipeline always proceeds with whatever the lake currently has.

    Returns a list of `{name, ok, error?}` dicts — one per sub-step. The
    caller attaches these to the run-log so a silent token failure shows up
    on /pipeline and triggers the analytics_token_failure rule.
    """
    steps = [
        ("analytics.discover", [sys.executable, "-m", "skills.analytics.discover"]),
        ("analytics.gsc_pull", [sys.executable, "-m", "skills.analytics.gsc_pull"]),
        ("analytics.ga4_pull", [sys.executable, "-m", "skills.analytics.ga4_pull"]),
    ]
    # 30-min subprocess cap. Empirically GA4 + GSC pulls take 10-15 min each on
    # a cold runner (5 token sessions x 30-100 properties + 7-day window per
    # property). The old 10-min cap timed out routinely. Daily incrementals
    # fit in <60s once the lake has history, so the higher cap doesn't slow
    # the steady state.
    _TIMEOUT_S = 1800
    results: list[dict] = []
    for label, cmd in steps:
        print(f"Pulling {label}...")
        try:
            subprocess.run(cmd, check=True, timeout=_TIMEOUT_S,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
            results.append({"name": label, "ok": True})
        except subprocess.CalledProcessError as e:
            tail = ((e.stderr or "") + (e.stdout or "")).strip()[-500:] or str(e)
            print(f"{label} skipped: {tail}", file=sys.stderr)
            results.append({"name": label, "ok": False, "error": tail})
        except subprocess.TimeoutExpired:
            err = f"timeout after {_TIMEOUT_S}s"
            print(f"{label} skipped: {err}", file=sys.stderr)
            results.append({"name": label, "ok": False, "error": err})
        except Exception as e:                              # pragma: no cover
            err = f"{type(e).__name__}: {e}"
            print(f"{label} skipped: {err}", file=sys.stderr)
            results.append({"name": label, "ok": False, "error": err})
    return results


def sync_fleet_db(db_path=FLEET_DB, *, snapshots, daily_rows, interventions,
                  today) -> None:
    """Rebuild fleet.db and recompute effectiveness. Failure-isolated —
    a DB problem must never abort the pipeline run."""
    try:
        fleet_db_mod.sync(db_path, snapshots=snapshots, daily_rows=daily_rows,
                          interventions=interventions)
        effectiveness_mod.compute(db_path, today=today)
    except Exception as e:                            # pragma: no cover
        print(f"fleet.db sync skipped: {e}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description="Fleet monitoring orchestrator")
    p.add_argument("--collect", action="store_true")
    p.add_argument("--analyze", action="store_true")
    p.add_argument("--render", action="store_true")
    p.add_argument("--no-probes", action="store_true", help="collect without bot probes")
    args = p.parse_args()
    do_all = not (args.collect or args.analyze or args.render)

    stages: list[dict] = []
    run_started = time.monotonic()
    alerts: list[dict] = []
    snapshot: dict = {}
    status, error = "ok", None
    try:
        # R2 sync (no-op when R2 env vars absent — developer-laptop runs).
        # Pull state from R2 BEFORE collect so we operate on the canonical
        # history. Skipped when running ONLY --render against existing
        # local state.
        if do_all or args.collect:
            with _stage("r2_pull", stages):
                r2_state_mod.pull_from_r2()

        if do_all or args.collect:
            analytics_sub: list[dict] = []
            with _stage("analytics_pull", stages, sub_steps=analytics_sub):
                analytics_sub.extend(_pull_analytics_lake())
            with _stage("collect", stages):
                asyncio.run(collect_mod.collect(run_probes=not args.no_probes))

        if do_all or args.analyze:
            with _stage("analyze", stages):
                # Pass in-flight stages so analytics_token_failure rule sees
                # THIS run's analytics_pull sub_steps (chicken-and-egg fix:
                # the run-log entry for this run isn't written until the
                # finally: block — without this, the rule reads yesterday's
                # disk entry and fires stale alerts).
                snapshot, alert_objs = analyze_mod.analyze(
                    current_run_stages=stages)
                alerts = [a.to_dict() for a in alert_objs]
            with _stage("timeseries", stages):
                append_rollup(rollup_rows(snapshot))
                append_daily(daily_rollup_rows(snapshot))
            with _stage("fleet_db", stages):
                sync_fleet_db(
                    snapshots=load_snapshots(),
                    daily_rows=read_daily_all(),
                    interventions=load_interventions(),
                    today=_date.today().isoformat())

        if do_all or args.render:
            with _stage("render", stages):
                render_mod.render()

        if snapshot:
            print(headline(snapshot, alerts))
    except Exception as e:
        status, error = "failed", f"{type(e).__name__}: {e}"
        print(f"RUN FAILED: {e}", file=sys.stderr)
    finally:
        # Always write a run-log entry — even on failure — so the /pipeline
        # page surfaces the breakage rather than silently dropping it.
        counts = {"new": sum(1 for a in alerts if a["state"] == "new"),
                  "ongoing": sum(1 for a in alerts if a["state"] == "ongoing"),
                  "resolved": sum(1 for a in alerts if a["state"] == "resolved"),
                  "muted": sum(1 for a in alerts if a["state"] == "muted")}
        total_s = int(time.monotonic() - run_started)
        _append_run_log(run_log_entry(
            snapshot.get("date") or _date.today().isoformat(),
            snapshot.get("run", {}).get("duration_s", total_s),
            snapshot.get("run", {}).get("coverage", {}),
            counts, stages=stages, status=status, error=error))
        # Push state back to R2 LAST, after the run-log entry is written —
        # so a failed run still uploads the entry describing what broke.
        # Wrapped in its own stage so failures here don't mask the prior
        # error, but the run-log entry that was just written gets pushed.
        try:
            push_started = time.monotonic()
            push_result = r2_state_mod.push_to_r2()
            if "skipped" not in push_result:
                print(f"R2 push: {push_result.get('files_pushed', 0)} files "
                      f"in {round(time.monotonic() - push_started, 1)}s")
        except Exception as e:                                # pragma: no cover
            print(f"R2 push failed: {e}", file=sys.stderr)
        if status == "failed":
            sys.exit(1)


if __name__ == "__main__":
    main()
