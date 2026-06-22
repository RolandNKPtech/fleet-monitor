"""WP Engine API helpers — install enumeration + per-install usage rollup.

Sync (urllib) — mirrors the proven approach in scripts/monitor_fixed_sites.py.
Call the network functions via asyncio.to_thread from the async collect stage.
"""
from __future__ import annotations
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

_WPE_USER = os.environ.get("WPE_API_USER", "")
_WPE_PW = os.environ.get("WPE_API_PASSWORD", "")
_WPE_AUTH = base64.b64encode(f"{_WPE_USER}:{_WPE_PW}".encode()).decode()
_SSL_CTX = ssl.create_default_context()


def _wpe_get(path: str) -> dict | None:
    """GET https://api.wpengineapi.com/v1{path}. Returns parsed JSON or None on error."""
    url = f"https://api.wpengineapi.com/v1{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {_WPE_AUTH}"})
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as r:
            return json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  WPE API error on {path}: {e}", file=sys.stderr)
        return None


def list_installs() -> list[dict]:
    """Paginated /installs scan across all accounts the token can see.

    Returns [{name, id, account_id, primary_domain, cname}, ...].
    """
    out: list[dict] = []
    offset = 0
    while True:
        data = _wpe_get(f"/installs?limit=100&offset={offset}")
        if not data:
            break
        for inst in data.get("results", []):
            if inst.get("name"):
                out.append({
                    "name": inst["name"],
                    "id": inst.get("id"),
                    "account_id": (inst.get("account") or {}).get("id"),
                    "primary_domain": inst.get("primary_domain"),
                    "cname": inst.get("cname"),
                })
        if offset + 100 >= data.get("count", 0):
            break
        offset += 100
    return out


def get_install_usage(install_id: str) -> dict | None:
    """Raw /installs/{id}/usage response, or None on error."""
    return _wpe_get(f"/installs/{install_id}/usage")


def list_accounts() -> list[dict]:
    """All accounts visible to the token. Returns [{id, name}, ...].

    The install endpoint only carries `account.id` — names live here. The fleet
    is small (~6 accounts), so one call is enough; no pagination needed today.
    """
    data = _wpe_get("/accounts")
    if not data:
        return []
    return [{"id": a.get("id"), "name": a.get("name")}
            for a in data.get("results", []) if a.get("id")]


def parse_usage_rollup(usage: dict | None) -> dict | None:
    """Reduce a /usage response to the metrics we store. None if no rollup present."""
    if not usage or "metrics_rollup" not in usage:
        return None
    r = usage["metrics_rollup"]
    bw = int((r.get("network_total_bytes") or {}).get("sum", 0) or 0)
    origin = int((r.get("network_origin_bytes") or {}).get("sum", 0) or 0)
    cdn = int((r.get("network_cdn_bytes") or {}).get("sum", 0) or 0)
    visits = int((r.get("billable_visits") or {}).get("sum", 0) or 0)
    total_visits = int((r.get("visit_count") or {}).get("sum", 0) or 0)
    storage_file = int(((r.get("storage_file_bytes") or {}).get("latest") or {}).get("value", 0) or 0)
    storage_db = int(((r.get("storage_database_bytes") or {}).get("latest") or {}).get("value", 0) or 0)
    storage = storage_file + storage_db
    mb_per_visit = (bw / 1e6 / visits) if visits else 0
    return {
        "bandwidth_gb_30d": round(bw / 1e9, 2),
        "origin_gb_30d": round(origin / 1e9, 2),
        "cdn_gb_30d": round(cdn / 1e9, 2),
        "billable_visits_30d": visits,
        "total_visits_30d": total_visits,
        "storage_gb": round(storage / 1e9, 2),
        "mb_per_visit": round(mb_per_visit, 1),
    }


def parse_usage_daily(usage: dict | None) -> list[dict]:
    """Normalize the daily metrics array WPE returns inside /installs/{id}/usage.

    WPE returns `usage["metrics"]` — typically 31 entries. Each row carries
    `date` plus byte and visit counters that may be string-encoded ints OR
    None.  We coerce to plain int and zero-fill None so downstream sums are
    safe.  Returns [] when the field is absent.
    """
    if not usage:
        return []
    raw = usage.get("metrics") or []
    out: list[dict] = []
    for d in raw:
        if not d or not d.get("date"):
            continue
        out.append({
            "date": d["date"],
            "network_total_bytes": int(d.get("network_total_bytes") or 0),
            "network_origin_bytes": int(d.get("network_origin_bytes") or 0),
            "network_cdn_bytes": int(d.get("network_cdn_bytes") or 0),
            "billable_visits": int(d.get("billable_visits") or 0),
            "visit_count": int(d.get("visit_count") or 0),
            "storage_file_bytes": int(d.get("storage_file_bytes") or 0),
            "storage_database_bytes": int(d.get("storage_database_bytes") or 0),
        })
    return out
