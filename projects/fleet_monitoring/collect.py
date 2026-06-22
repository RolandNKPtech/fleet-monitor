"""Collect stage — discover the fleet roster and pull WPE + CF metrics into a snapshot.

Public surface:
  assemble_snapshot(...)  pure — merges already-fetched data into the snapshot dict
  collect()               async — does the network I/O, then calls assemble_snapshot
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from datetime import date as _date, datetime, timezone

from skills.cloudflare.client import get_cf_client
from . import wpe_api, cf_api, roster as roster_mod, overlay as overlay_mod, probes as probes_mod
from . import analytics_map, analytics_pull
from .models import SCHEMA_VERSION, SNAPSHOTS_DIR, ANALYTICS_LAKE_DIR, ANALYTICS_OVERRIDES_FILE

# Pipeline launch date — anchor for the probe-rotation index (days-since-launch).
_PROBE_ROTATION_EPOCH = _date(2026, 5, 16)

# Max simultaneous WPE /usage calls. 248 unbounded thread requests overwhelmed
# the WPE API (every call 500'd). 8 is plenty for the 10-minute run budget.
_WPE_CONCURRENCY = 8


def assemble_snapshot(today: str, roster: list[dict], wpe_metrics: dict[str, dict],
                      cf_configs: dict[str, dict], cf_analytics: dict[str, dict],
                      overlay_idx: dict[str, dict], probes: dict[str, dict],
                      duration_s: int = 0,
                      accounts: dict[str, str] | None = None,
                      wpe_daily: dict[str, list[dict]] | None = None,
                      per_site: dict[str, dict] | None = None,
                      analytics: dict[str, dict] | None = None,
                      cf_cert_expiry: dict[str, dict] | None = None) -> dict:
    """Merge already-fetched data into the snapshot structure. Pure — no I/O.

    `accounts`       — optional {uuid: name} used to enrich each wpe block with
                       the friendly account name alongside the UUID.
    `wpe_daily`      — optional {install_id: [daily-row, ...]} attached as
                       wpe.daily so the audit trail keeps the per-day metrics
                       WPE returns inside /installs/{id}/usage.
    `per_site`       — optional {zone_id: per_site_block} attached as cf.per_site
                       on each site entry that has a matching zone.
    `analytics`      — optional {apex: {ga4: dict|None, gsc: dict|None}} from
                       analytics_pull.pull(); None means no coverage and is
                       surfaced honestly rather than fabricated as a zero.
    `cf_cert_expiry` — optional {zone_id: {min_days_until_expiry: int|None,
                       ...}} attached as cf.cert_expiry. Kept SEPARATE from
                       cf.config so the daily countdown doesn't pollute the
                       drift digest.
    """
    accounts = accounts or {}
    wpe_daily = wpe_daily or {}
    per_site = per_site or {}
    analytics = analytics or {}
    cf_cert_expiry = cf_cert_expiry or {}
    sites = []
    wpe_ok = wpe_total = 0
    cfg_ok = cfg_total = 0
    for r in roster:
        entry = {
            "key": r["key"], "apex": r["apex"], "join_state": r["join_state"],
            "wpe": None, "cf": None, "probe": None, "overlay": None,
        }
        if r.get("wpe_install_id"):
            wpe_total += 1
            m = wpe_metrics.get(r["wpe_install_id"])
            if m:
                wpe_ok += 1
                entry["wpe"] = {
                    "install": r["wpe_install"],
                    "account": r["wpe_account"],
                    "account_name": accounts.get(r["wpe_account"]),
                    "daily": wpe_daily.get(r["wpe_install_id"], []),
                    **m,
                }
        if r.get("cf_zone_id"):
            cfg_total += 1
            cfg = cf_configs.get(r["cf_zone_id"]) or {}
            an = cf_analytics.get(r["cf_zone_id"]) or {}
            if cfg and "error" not in cfg:
                cfg_ok += 1
            cf_entry = {"zone_id": r["cf_zone_id"], "config": cfg, "analytics": an}
            ps = per_site.get(r["cf_zone_id"])
            if ps:
                cf_entry["per_site"] = ps
            ce = cf_cert_expiry.get(r["cf_zone_id"])
            if ce:
                cf_entry["cert_expiry"] = ce
            if r.get("cf_plan"):
                cf_entry["plan"] = r["cf_plan"]
            entry["cf"] = cf_entry
        entry["overlay"] = overlay_idx.get(r["key"])
        entry["probe"] = probes.get(r["key"])
        entry["analytics"] = analytics.get(r["key"]) or {"ga4": None, "gsc": None}
        sites.append(entry)

    return {
        "schema_version": SCHEMA_VERSION,
        "date": today,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "run": {
            "duration_s": duration_s,
            "coverage": {
                "wpe": f"{wpe_ok}/{wpe_total}",
                "cf_config": f"{cfg_ok}/{cfg_total}",
            },
        },
        "roster_summary": {
            "total": len(roster),
            "wpe+cf": sum(1 for r in roster if r["join_state"] == "wpe+cf"),
            "wpe-only": sum(1 for r in roster if r["join_state"] == "wpe-only"),
            "cf-only": sum(1 for r in roster if r["join_state"] == "cf-only"),
        },
        "sites": sites,
    }


def write_snapshot(snapshot: dict) -> "Path":
    """Write the dated snapshot JSON. Returns the path."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOTS_DIR / f"{snapshot['date']}.json"
    out.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    return out


async def collect(run_probes: bool = True) -> dict:
    """Full network collect. Returns the snapshot dict (also written to disk)."""
    started = datetime.now(timezone.utc)
    today = _date.today().isoformat()
    print("Discovering fleet roster...")
    installs = await asyncio.to_thread(wpe_api.list_installs)
    accounts_list = await asyncio.to_thread(wpe_api.list_accounts)
    accounts_by_id = {a["id"]: a["name"] for a in accounts_list if a.get("id")}
    client = get_cf_client()
    zones = await client.get_all_zones()
    roster = roster_mod.join_roster(installs, zones)
    print(f"  {len(roster)} sites ({len(installs)} WPE installs, "
          f"{len(zones)} CF zones, {len(accounts_by_id)} WPE accounts)")

    # WPE usage — sync calls in threads, bounded by a semaphore to avoid the
    # WPE API 500ing under a thundering herd. 248 unbounded threads overwhelmed
    # it on first try; 8 concurrent is plenty.
    print("Pulling WPE usage...")
    wpe_metrics: dict[str, dict] = {}
    wpe_daily: dict[str, list[dict]] = {}
    wpe_sem = asyncio.Semaphore(_WPE_CONCURRENCY)

    def _pull_one(install_id: str) -> tuple[str, dict | None, list[dict]]:
        usage = wpe_api.get_install_usage(install_id)
        return (install_id,
                wpe_api.parse_usage_rollup(usage),
                wpe_api.parse_usage_daily(usage))

    async def _bounded_pull(install_id: str) -> tuple[str, dict | None, list[dict]]:
        async with wpe_sem:
            return await asyncio.to_thread(_pull_one, install_id)

    install_ids = [r["wpe_install_id"] for r in roster if r.get("wpe_install_id")]
    results = await asyncio.gather(*(_bounded_pull(iid) for iid in install_ids))
    for iid, m, daily in results:
        if m:
            wpe_metrics[iid] = m
        if daily:
            wpe_daily[iid] = daily
    print(f"  WPE usage: {len(wpe_metrics)}/{len(install_ids)} ok")

    # CF config + analytics + cert expiry — async, client semaphore bounds concurrency
    print("Pulling CF config + analytics + cert expiry...")
    cf_configs: dict[str, dict] = {}
    cf_analytics: dict[str, dict] = {}
    cf_cert_expiry: dict[str, dict] = {}
    zone_jobs = [(r["cf_zone_id"], r["apex"]) for r in roster if r.get("cf_zone_id")]
    cfg_results = await asyncio.gather(
        *(cf_api.fetch_zone_config(client, zid, apex) for zid, apex in zone_jobs))
    an_results = await asyncio.gather(
        *(cf_api.fetch_zone_analytics(client, zid) for zid, _ in zone_jobs))
    cert_results = await asyncio.gather(
        *(cf_api.fetch_zone_cert_expiry(client, zid) for zid, _ in zone_jobs))
    for (zid, _), cfg, an, ce in zip(zone_jobs, cfg_results, an_results, cert_results):
        cf_configs[zid] = cfg
        cf_analytics[zid] = an
        cf_cert_expiry[zid] = ce

    # Per-site CF analytics (countries / requests+threats / top paths+UAs)
    # — the long pole on collect. Skipped in --fast mode; sites keep
    # their last-known per_site block carried from the previous snapshot.
    # Per-site CF analytics (countries / requests+threats / top paths+UAs)
    print("Pulling per-site CF analytics (countries / threats / top-paths-UAs)...")
    from . import cf_per_site as cps_mod
    cps_sem = asyncio.Semaphore(10)

    async def _bounded_cps(zone_id: str) -> tuple[str, dict]:
        async with cps_sem:
            try:
                r = await cps_mod.fetch_all_for_zone(client, zone_id)
            except Exception as e:                       # pragma: no cover
                r = {"error": True, "exception": str(e), "countries": [],
                     "requests_threats_daily": [], "top_paths": [], "top_uas": []}
            return (zone_id, r)

    per_site_jobs = [_bounded_cps(zid) for zid, _ in zone_jobs]
    per_site_results = await asyncio.gather(*per_site_jobs)
    per_site = {zid: r for zid, r in per_site_results}

    # Overlay
    overlay_idx = overlay_mod.build_overlay_index(overlay_mod.load_tracker())

    # Probes — managed set + rotating sample
    probes: dict[str, dict] = {}
    if run_probes:
        managed = set(overlay_idx.keys())
        # Rotation index = days since pipeline launch. Monotonic counter into
        # probes.select_probe_targets, which mods by the unmanaged-roster size.
        day_index = (_date.today() - _PROBE_ROTATION_EPOCH).days
        targets = probes_mod.select_probe_targets(roster, managed, day_index)
        print(f"Probing {len(targets)} sites...")
        for key in targets:
            probes[key] = await asyncio.to_thread(probes_mod.probe_site, key)

    duration_s = int((datetime.now(timezone.utc) - started).total_seconds())
    apexes = [r["apex"] for r in roster]
    mapping = analytics_map.build_mapping(
        apexes, lake_path=ANALYTICS_LAKE_DIR,
        overrides_path=ANALYTICS_OVERRIDES_FILE)
    analytics = analytics_pull.pull(
        mapping, today=_date.fromisoformat(today),
        lake_path=ANALYTICS_LAKE_DIR)
    snapshot = assemble_snapshot(today, roster, wpe_metrics, cf_configs,
                                 cf_analytics, overlay_idx, probes, duration_s,
                                 accounts=accounts_by_id, wpe_daily=wpe_daily,
                                 per_site=per_site, analytics=analytics,
                                 cf_cert_expiry=cf_cert_expiry)
    out = write_snapshot(snapshot)
    roster_mod.save_roster(roster)
    print(f"Snapshot written: {out}")
    return snapshot


def main():
    p = argparse.ArgumentParser(description="Fleet monitoring — collect stage")
    p.add_argument("--no-probes", action="store_true", help="Skip bot probes")
    args = p.parse_args()
    try:
        asyncio.run(collect(run_probes=not args.no_probes))
    except Exception as e:
        print(f"COLLECT FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
