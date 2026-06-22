"""SQLite analytical store — interventions + per-site metrics mirror + effectiveness.

`data/fleet.db` is DERIVED — fully rebuilt from daily.jsonl + snapshots +
interventions.yml on every run. Never hand-edited.
"""
from __future__ import annotations
import sqlite3

from .models import FLEET_DB

_SCHEMA = """
CREATE TABLE metrics (
    date            TEXT NOT NULL,
    site_key        TEXT NOT NULL,
    bandwidth_gb    REAL,
    billable_visits INTEGER,
    mb_per_visit    REAL,
    storage_gb      REAL,
    cache_hit_rate  REAL,
    PRIMARY KEY (date, site_key)
);
CREATE TABLE interventions (
    id            INTEGER PRIMARY KEY,
    site_key      TEXT NOT NULL,
    applied_date  TEXT NOT NULL,
    type          TEXT NOT NULL,
    target_metric TEXT NOT NULL,
    description   TEXT,
    fingerprint   TEXT NOT NULL UNIQUE
);
CREATE TABLE effectiveness (
    intervention_id INTEGER NOT NULL REFERENCES interventions(id),
    horizon_days    INTEGER NOT NULL,
    before_avg      REAL,
    after_avg       REAL,
    delta_pct       REAL,
    verdict         TEXT NOT NULL,
    PRIMARY KEY (intervention_id, horizon_days)
);
"""


def _install_to_key(snapshots: list[dict]) -> dict[str, str]:
    """Map WPE install id -> site key, from the latest snapshot."""
    if not snapshots:
        return {}
    latest = snapshots[-1]
    out = {}
    for s in latest.get("sites", []):
        wpe = s.get("wpe") or {}
        install = wpe.get("install")
        if install and s.get("key"):
            out[install] = s["key"]
    return out


def _metric_rows(daily_rows, install_key, snapshots) -> list[dict]:
    """Build (date, site_key) metric rows from daily.jsonl + snapshot cache rate."""
    rows: dict[tuple, dict] = {}
    for d in daily_rows:
        install = d.get("install")
        site_key = install_key.get(install)
        if not site_key:
            continue
        total_bytes = int(d.get("network_total_bytes") or 0)
        visits = int(d.get("billable_visits") or 0)
        storage = (int(d.get("storage_file_bytes") or 0)
                   + int(d.get("storage_database_bytes") or 0))
        rows[(d["date"], site_key)] = {
            "date": d["date"], "site_key": site_key,
            "bandwidth_gb": total_bytes / 1e9,
            "billable_visits": visits,
            "mb_per_visit": (total_bytes / 1e6 / visits) if visits else 0.0,
            "storage_gb": storage / 1e9,
            "cache_hit_rate": None,
        }
    for snap in snapshots:
        sd = snap.get("date")
        for s in snap.get("sites", []):
            key = s.get("key")
            chr_ = ((s.get("cf") or {}).get("analytics") or {}).get("cache_hit_rate")
            if not key or chr_ is None:
                continue
            existing = rows.get((sd, key))
            if existing:
                existing["cache_hit_rate"] = chr_
    return list(rows.values())


def sync(db_path=FLEET_DB, *, snapshots: list[dict], daily_rows: list[dict],
         interventions: list[dict]) -> None:
    """Rebuild fleet.db from scratch. Idempotent — the DB is purely derived."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        install_key = _install_to_key(snapshots)
        metric_rows = _metric_rows(daily_rows, install_key, snapshots)
        conn.executemany(
            "INSERT OR REPLACE INTO metrics "
            "(date, site_key, bandwidth_gb, billable_visits, mb_per_visit, "
            " storage_gb, cache_hit_rate) VALUES "
            "(:date, :site_key, :bandwidth_gb, :billable_visits, :mb_per_visit, "
            " :storage_gb, :cache_hit_rate)", metric_rows)
        confirmed = [i for i in interventions if i.get("status") == "confirmed"]
        conn.executemany(
            "INSERT INTO interventions "
            "(site_key, applied_date, type, target_metric, description, fingerprint) "
            "VALUES (:site, :applied_date, :type, :target_metric, "
            ":description, :fingerprint)",
            [{"site": i.get("site"), "applied_date": i.get("applied_date"),
              "type": i.get("type"), "target_metric": i.get("target_metric"),
              "description": i.get("description"),
              "fingerprint": i.get("fingerprint")} for i in confirmed])
        conn.commit()
    finally:
        conn.close()


def query(db_path, sql: str, params=()) -> list[dict]:
    """Read-only query helper — returns rows as dicts."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
