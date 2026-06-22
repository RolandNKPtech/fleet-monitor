"""Compact append-only per-site daily rollup. Charts read this, never the raw snapshots."""
from __future__ import annotations
import json

from .models import TIMESERIES_FILE, DAILY_FILE


def rollup_rows(snapshot: dict) -> list[dict]:
    """One compact row per site from a full snapshot.

    `account` is the friendly WPE account name (e.g. "acctA") so per-account
    aggregation by date is a one-pass groupby on this file — no need to re-read
    the raw snapshots.
    """
    rows = []
    for s in snapshot.get("sites", []):
        wpe = s.get("wpe") or {}
        cf_an = (s.get("cf") or {}).get("analytics") or {}
        ga4 = (s.get("analytics") or {}).get("ga4") or {}
        gsc = (s.get("analytics") or {}).get("gsc") or {}
        rows.append({
            "date": snapshot["date"],
            "key": s["key"],
            "account": wpe.get("account_name"),
            "bandwidth_gb": wpe.get("bandwidth_gb_30d"),
            "billable_visits": wpe.get("billable_visits_30d"),
            "mb_per_visit": wpe.get("mb_per_visit"),
            "cache_hit_rate": cf_an.get("cache_hit_rate"),
            "threats": cf_an.get("threats"),
            "alert_count": s.get("alerts_count", 0),
            # New: analytics — None when the site lacks GA4/GSC coverage.
            "ga4_sessions": ga4.get("sessions_7d"),
            "ga4_conversions": ga4.get("conversions_7d"),
            "gsc_clicks": gsc.get("clicks_7d"),
        })
    return rows


def append_rollup(rows: list[dict]) -> None:
    """Replace-or-append rollup rows in timeseries.jsonl.

    Idempotent per-date: re-running the pipeline on the same day replaces
    that day's rows instead of duplicating them. The on-disk format is still
    one JSON object per line; the file is rewritten when a same-date set
    arrives (cheap — file is one short line per site per day).
    """
    if not rows:
        return
    incoming_dates = {r["date"] for r in rows if r.get("date")}
    TIMESERIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TIMESERIES_FILE.exists() and incoming_dates:
        kept = [r for r in read_all() if r.get("date") not in incoming_dates]
        kept.extend(rows)
        TIMESERIES_FILE.write_text(
            "\n".join(json.dumps(r) for r in kept) + "\n", encoding="utf-8")
    else:
        with TIMESERIES_FILE.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")


def read_all() -> list[dict]:
    """Read every rollup row. Cheap — the file is one short line per site per day."""
    if not TIMESERIES_FILE.exists():
        return []
    out = []
    for line in TIMESERIES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def read_series(key: str) -> list[dict]:
    """All rollup rows for one site, oldest first."""
    return [r for r in read_all() if r.get("key") == key]


def daily_rollup_rows(snapshot: dict) -> list[dict]:
    """Flatten a snapshot's per-install daily arrays into one row per (install, date).

    Skips sites without a wpe block.  Each row carries account_name + account_id
    so per-account aggregation downstream is a simple groupby.
    """
    rows = []
    for s in snapshot.get("sites", []):
        wpe = s.get("wpe") or {}
        daily = wpe.get("daily") or []
        if not daily:
            continue
        install = wpe.get("install") or s.get("key")
        account = wpe.get("account_name") or wpe.get("account")
        account_id = wpe.get("account")     # raw UUID lives in `account` field
        # Drop rows with no account anchor — account-keyed aggregation
        # downstream (plan_utilization, render panel) would silently
        # mis-group them under None and the operator would see 0% used.
        if not account:
            continue
        for d in daily:
            rows.append({
                "date": d["date"],
                "install": install,
                "account": account,
                "account_id": account_id,
                "network_total_bytes": int(d.get("network_total_bytes") or 0),
                "network_origin_bytes": int(d.get("network_origin_bytes") or 0),
                "network_cdn_bytes": int(d.get("network_cdn_bytes") or 0),
                "billable_visits": int(d.get("billable_visits") or 0),
                "visit_count": int(d.get("visit_count") or 0),
                "storage_file_bytes": int(d.get("storage_file_bytes") or 0),
                "storage_database_bytes": int(d.get("storage_database_bytes") or 0),
            })
    return rows


def append_daily(rows: list[dict]) -> None:
    """Replace-or-append rows in daily.jsonl, idempotent per-date.

    Rerunning the pipeline on a day already present rewrites the file with
    that day's rows replaced — exactly mirrors append_rollup's behaviour.
    """
    if not rows:
        return
    incoming_dates = {r["date"] for r in rows if r.get("date")}
    DAILY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if DAILY_FILE.exists() and incoming_dates:
        kept = [r for r in read_daily_all() if r.get("date") not in incoming_dates]
        kept.extend(rows)
        DAILY_FILE.write_text(
            "\n".join(json.dumps(r) for r in kept) + "\n", encoding="utf-8")
    else:
        with DAILY_FILE.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")


def read_daily_all() -> list[dict]:
    """Every row in daily.jsonl."""
    if not DAILY_FILE.exists():
        return []
    out = []
    for line in DAILY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out
