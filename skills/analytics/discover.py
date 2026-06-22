"""Discovery — enumerate every GA4 property and GSC site reachable from our live tokens.

Writes:
  data/analytics/meta/properties.parquet  -- one row per (token, property)
  data/analytics/meta/sites.parquet       -- one row per (token, site)
  data/analytics/meta/token_health.parquet -- one row per token (ok/dead, counts)

Idempotent: each run overwrites the meta tables with the current state.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .oauth import TokenSession, api_get, load_sessions

REPO_ROOT = Path(__file__).resolve().parents[2]
META_DIR = REPO_ROOT / "data" / "analytics" / "meta"

GA4_ADMIN = "https://analyticsadmin.googleapis.com/v1beta"
GSC = "https://searchconsole.googleapis.com/webmasters/v3"


def _normalize_host(site_url: str) -> str:
    if site_url.startswith("sc-domain:"):
        return site_url[len("sc-domain:"):].lower().strip("/")
    return site_url.replace("https://", "").replace("http://", "").lower().strip("/")


def list_ga4_properties(session: TokenSession) -> tuple[list[dict], str | None]:
    out: list[dict] = []
    page_token = ""
    while True:
        url = f"{GA4_ADMIN}/accountSummaries?pageSize=200"
        if page_token:
            url += f"&pageToken={urllib.parse.quote(page_token)}"
        status, body = api_get(session, url)
        if status != 200 or not isinstance(body, dict):
            err = body.get("error", {}).get("message") if isinstance(body, dict) else str(body)
            return out, f"GA4 list failed ({status}): {err}"
        for summary in body.get("accountSummaries", []):
            account_name = summary.get("displayName", "")
            account_id = summary.get("account", "").split("/")[-1]
            for prop in summary.get("propertySummaries", []) or []:
                out.append({
                    "account": session.label,
                    "account_id": account_id,
                    "account_name": account_name,
                    "property_id": prop.get("property", "").split("/")[-1],
                    "property_name": prop.get("displayName", ""),
                })
        page_token = body.get("nextPageToken", "")
        if not page_token:
            return out, None


def list_gsc_sites(session: TokenSession) -> tuple[list[dict], str | None]:
    status, body = api_get(session, f"{GSC}/sites")
    if status != 200 or not isinstance(body, dict):
        err = body.get("error", {}).get("message") if isinstance(body, dict) else str(body)
        return [], f"GSC list failed ({status}): {err}"
    rows = []
    for s in body.get("siteEntry", []) or []:
        site_url = s.get("siteUrl", "")
        rows.append({
            "account": session.label,
            "site_url": site_url,
            "host": _normalize_host(site_url),
            "permission_level": s.get("permissionLevel", ""),
        })
    return rows, None


def _write_parquet(rows: list[dict], path: Path, schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # Write empty table with declared schema so downstream readers don't fail.
        pq.write_table(pa.Table.from_pylist([], schema=schema), path)
        return
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def run() -> dict:
    """Discover everything; return summary dict."""
    META_DIR.mkdir(parents=True, exist_ok=True)
    sessions = load_sessions()

    ga4_rows: list[dict] = []
    gsc_rows: list[dict] = []
    health: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for s in sessions:
        if not s.ensure_fresh():
            health.append({
                "account": s.label,
                "status": "dead_token",
                "ga4_properties": 0,
                "gsc_sites": 0,
                "error": s.last_error,
                "checked_at": now,
            })
            print(f"[{s.label}] DEAD: {s.last_error}")
            continue

        ga4_props, ga4_err = list_ga4_properties(s)
        ga4_rows.extend(ga4_props)

        gsc_sites, gsc_err = list_gsc_sites(s)
        gsc_rows.extend(gsc_sites)

        health.append({
            "account": s.label,
            "status": "ok" if not (ga4_err or gsc_err) else "partial",
            "ga4_properties": len(ga4_props),
            "gsc_sites": len(gsc_sites),
            "error": "; ".join(e for e in (ga4_err, gsc_err) if e) or "",
            "checked_at": now,
        })
        print(f"[{s.label}] GA4={len(ga4_props)}  GSC={len(gsc_sites)}"
              + (f"  ga4_err={ga4_err}" if ga4_err else "")
              + (f"  gsc_err={gsc_err}" if gsc_err else ""))

    ga4_schema = pa.schema([
        ("account", pa.string()), ("account_id", pa.string()), ("account_name", pa.string()),
        ("property_id", pa.string()), ("property_name", pa.string()),
    ])
    gsc_schema = pa.schema([
        ("account", pa.string()), ("site_url", pa.string()),
        ("host", pa.string()), ("permission_level", pa.string()),
    ])
    health_schema = pa.schema([
        ("account", pa.string()), ("status", pa.string()),
        ("ga4_properties", pa.int64()), ("gsc_sites", pa.int64()),
        ("error", pa.string()), ("checked_at", pa.string()),
    ])

    _write_parquet(ga4_rows, META_DIR / "properties.parquet", ga4_schema)
    _write_parquet(gsc_rows, META_DIR / "sites.parquet", gsc_schema)
    _write_parquet(health, META_DIR / "token_health.parquet", health_schema)

    return {
        "ga4_property_grants": len(ga4_rows),
        "ga4_unique_properties": len({r["property_id"] for r in ga4_rows if r["property_id"]}),
        "gsc_site_grants": len(gsc_rows),
        "gsc_unique_hosts": len({r["host"] for r in gsc_rows if r["host"]}),
        "tokens_ok": sum(1 for h in health if h["status"] == "ok"),
        "tokens_dead": sum(1 for h in health if h["status"] == "dead_token"),
    }


if __name__ == "__main__":
    summary = run()
    print("\nDiscovery summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
