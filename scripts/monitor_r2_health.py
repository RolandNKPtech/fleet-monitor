"""Daily R2 health monitor for the 20 R2-offloaded sites in the tracker.

For each R2 site:
  1. SSH to its WPE install
  2. Run a `wp eval-file` scan: probe the thumbnail variant of every image
     attachment uploaded in the last 30 days (server-side HEAD checks)
  3. Return the broken_count + broken_ids

Output:
  data/reports/r2-health/{date}.json   — per-site results for the run
  data/reports/r2-health/latest.json   — symlink/copy of today's run
  data/reports/r2-health/history.jsonl — append-only history of daily counts

Companion: scripts/sync_r2_health_to_sheet.py writes the same data to a new
"R2 Health" tab on the WPE Bandwidth sheet.

Run:
  python scripts/monitor_r2_health.py
  python scripts/monitor_r2_health.py --site dsmcoachlight.com   # one site
  python scripts/monitor_r2_health.py --days 60                  # bigger window
"""
from __future__ import annotations
import asyncio, sys, base64, json, argparse
from pathlib import Path
from datetime import date, datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import yaml
from skills.wpengine.ssh import WPESSHClient

TRACKER = ROOT / "data/reports/fleet-bandwidth-audit-2026-04-30/monitoring/fixed-sites.yml"
WPE_INSTALLS_CACHE = ROOT / "data/reports/wpe-all-installs-2026-06-08.json"
OUTDIR = ROOT / "data/reports/r2-health"
TODAY = date.today().isoformat()

# Path on WPE filesystem where the R2 uploader mu-plugin lives.
# Presence of this file = site is R2-offloaded.
PLUGIN_PROBE_PATH = "wp-content/mu-plugins/nkp-r2-uploader.php"


# Server-side PHP scan: probe thumbnail of recent image attachments
SCAN_PHP_TEMPLATE = """<?php
global $wpdb;
$days = __DAYS__;
$cutoff = date('Y-m-d', strtotime("-{$days} days"));
$ids = $wpdb->get_col($wpdb->prepare(
    "SELECT ID FROM {$wpdb->posts} WHERE post_type='attachment' AND post_mime_type LIKE 'image/%' AND post_date >= %s ORDER BY post_date DESC",
    $cutoff
));
$broken = [];
$probed = 0;
foreach ($ids as $id) {
    $meta = wp_get_attachment_metadata($id);
    if (empty($meta['sizes'])) continue;
    $thumb = $meta['sizes']['thumbnail'] ?? array_values($meta['sizes'])[0];
    if (empty($thumb['file'])) continue;
    $base_url = wp_get_attachment_url($id);
    $base_dir = dirname($base_url);
    $url = $base_dir . '/' . $thumb['file'];
    $resp = wp_remote_head($url, ['timeout' => 5, 'redirection' => 2]);
    $probed++;
    if (is_wp_error($resp)) continue;
    $code = wp_remote_retrieve_response_code($resp);
    if ($code !== 200) $broken[] = $id;
}
echo json_encode([
    'days' => $days,
    'cutoff' => $cutoff,
    'probed' => $probed,
    'broken' => count($broken),
    'broken_ids' => $broken,
]);
"""


def get_tracker_r2_sites():
    """Return list of {apex, install, bucket, source} for R2 sites in tracker."""
    t = yaml.safe_load(TRACKER.read_text(encoding="utf-8"))
    sites = t.get("sites", [])
    return [
        {"apex": s["apex"], "install": s.get("install"),
         "bucket": s.get("r2_bucket", ""), "source": "tracker"}
        for s in sites
        if s.get("r2_migration_date") or s.get("r2_bucket")
    ]


async def probe_install_for_plugin(install: dict, sem: asyncio.Semaphore) -> dict | None:
    """SSH a single install and check if the R2 uploader plugin file exists."""
    name = install["name"]
    env = install.get("environment", "")
    if env != "production":
        return None  # only monitor production installs
    if not name or "stg" in name or "dev" in name:
        return None
    async with sem:
        try:
            c = WPESSHClient(name)
            path = f"/nas/content/live/{name}/{PLUGIN_PROBE_PATH}"
            # test -f returns exit 0 if file exists, 1 otherwise
            r = await c.exec(f"test -f {path} && echo YES || echo NO", timeout=15)
            if r.stdout.strip() == "YES":
                pd = (install.get("primary_domain") or "").lower()
                apex = pd.removeprefix("www.").rstrip(".") if pd else name
                return {
                    "apex": apex,
                    "install": name,
                    "bucket": "",  # unknown without reading wp-config
                    "source": "discovered",
                }
            return None
        except Exception:
            return None  # can't connect — skip silently


async def discover_r2_installs(concurrency: int = 12) -> list[dict]:
    """Scan all production WPE installs for the R2 plugin presence."""
    if not WPE_INSTALLS_CACHE.exists():
        print(f"  WARN: WPE cache not found at {WPE_INSTALLS_CACHE.name}")
        return []
    installs = json.loads(WPE_INSTALLS_CACHE.read_text(encoding="utf-8"))
    prod = [i for i in installs if (i.get("environment") or "") == "production"]
    print(f"  Discovery: probing {len(prod)} production installs for R2 plugin...")
    sem = asyncio.Semaphore(concurrency)
    found = []
    tasks = [probe_install_for_plugin(i, sem) for i in prod]
    for fut in asyncio.as_completed(tasks):
        r = await fut
        if r:
            found.append(r)
    print(f"  Discovery: found {len(found)} installs with the R2 plugin")
    return found


def get_r2_sites_combined(tracker_sites: list, discovered: list) -> list[dict]:
    """Union tracker + discovered, dedup by install name (tracker wins)."""
    by_install = {s["install"]: s for s in tracker_sites if s.get("install")}
    for d in discovered:
        if d["install"] not in by_install:
            by_install[d["install"]] = d
    return list(by_install.values())


def get_r2_sites():
    """Legacy alias — tracker-only path. Kept for backward compat."""
    return get_tracker_r2_sites()


async def scan_one(site: dict, days: int, sem: asyncio.Semaphore) -> dict:
    """SSH to one install, run scan, return result."""
    async with sem:
        out = {
            "apex": site["apex"],
            "install": site["install"],
            "source": site.get("source", "tracker"),
            "scan_date": TODAY,
            "scan_ts": datetime.now(timezone.utc).isoformat(),
            "days_window": days,
        }
        if not site.get("install"):
            out["status"] = "no_install"
            return out

        try:
            c = WPESSHClient(site["install"])
            php = SCAN_PHP_TEMPLATE.replace("__DAYS__", str(days)).encode()
            b = base64.b64encode(php).decode()
            cmd = (
                f"cd ~/sites/{site['install']} && "
                f"printf %s {b} | base64 -d > /tmp/r2scan.php && "
                f"wp eval-file /tmp/r2scan.php; rm -f /tmp/r2scan.php"
            )
            r = await c.exec(cmd, timeout=600)
            if r.exit_code != 0:
                out["status"] = "scan_failed"
                out["error"] = (r.stderr or r.stdout or "")[:300]
                return out
            data = json.loads(r.stdout.strip())
            out.update({
                "status": "ok",
                "probed": data["probed"],
                "broken_count": data["broken"],
                "broken_ids": data["broken_ids"],
            })
            return out
        except Exception as e:
            out["status"] = "error"
            out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            return out


async def scan_all(days: int = 30, concurrency: int = 4,
                   site_filter: str | None = None,
                   include_discovered: bool = True,
                   verbose: bool = True) -> dict:
    """Scan every R2-offloaded install for broken thumbnails.

    Callable entry point for both the CLI (main()) and the pipeline's new
    `r2_health_scan` stage. Returns the full payload dict — caller decides
    whether to write to disk, push to R2, or both.

    Concurrency defaults to 4 to respect WPE SSH throttling (see
    feedback_wpe_ssh_paramiko_pitfalls). `verbose` controls per-site print
    output — leave True for CLI, set False when invoked from the pipeline
    stage so the run-log stays clean.
    """
    tracker_sites = get_tracker_r2_sites()
    if verbose:
        print(f"Tracker R2 sites: {len(tracker_sites)}")
    discovered = await discover_r2_installs(concurrency=12) if include_discovered else []

    sites = get_r2_sites_combined(tracker_sites, discovered)
    if site_filter:
        sites = [s for s in sites if s["apex"] == site_filter]
    if verbose:
        new_via_discovery = [s for s in sites if s.get("source") == "discovered"]
        if new_via_discovery:
            print(f"  Newly discovered (not yet in tracker): {len(new_via_discovery)}")
        print(f"\nScanning {len(sites)} R2 site(s), window={days}d, concurrency={concurrency}")

    sem = asyncio.Semaphore(concurrency)
    results = []
    tasks = [scan_one(s, days, sem) for s in sites]
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        r = await fut
        results.append(r)
        if verbose:
            status = r.get("status", "?")
            if status == "ok":
                print(f"  [{i}/{len(sites)}] {r['apex']:40s} probed={r['probed']:4d} broken={r['broken_count']}")
            else:
                print(f"  [{i}/{len(sites)}] {r['apex']:40s} {status}: {r.get('error','')[:80]}")

    return {
        "date": TODAY,
        "days_window": days,
        "results": results,
        "totals": {
            "sites_scanned": sum(1 for r in results if r.get("status") == "ok"),
            "sites_failed": sum(1 for r in results if r.get("status") != "ok"),
            "total_probed": sum(r.get("probed", 0) for r in results),
            "total_broken": sum(r.get("broken_count", 0) for r in results),
            "sites_with_broken": sum(1 for r in results if r.get("broken_count", 0) > 0),
        },
    }


def write_payload(payload: dict, push_to_r2: bool = True) -> None:
    """Persist a scan payload to data/reports/r2-health/ + optionally push
    to R2 so the public dashboard sees it on the next render. Split out so
    the CLI and the pipeline stage share identical write semantics."""
    OUTDIR.mkdir(parents=True, exist_ok=True)
    today = payload["date"]
    (OUTDIR / f"{today}.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (OUTDIR / "latest.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")
    history_line = json.dumps({"date": today, **payload["totals"]}, default=str)
    with (OUTDIR / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(history_line + "\n")
    if push_to_r2:
        from projects.fleet_monitoring.r2_state import push_one
        for local, remote in (
            (OUTDIR / "latest.json",      "fleet/r2-health/latest.json"),
            (OUTDIR / f"{today}.json",    f"fleet/r2-health/{today}.json"),
            (OUTDIR / "history.jsonl",    "fleet/r2-health/history.jsonl"),
        ):
            push_one(local, remote)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="Scan window in days")
    ap.add_argument("--site", help="Apex to scan (default: all R2 sites)")
    ap.add_argument("--no-discover", action="store_true",
                    help="Skip auto-discovery (use tracker only)")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    payload = await scan_all(
        days=args.days,
        concurrency=args.concurrency,
        site_filter=args.site,
        include_discovered=not args.no_discover,
        verbose=True,
    )
    write_payload(payload, push_to_r2=True)
    t = payload["totals"]
    print(f"\n=== Totals ===")
    print(f"  Sites scanned: {t['sites_scanned']} / {len(payload['results'])}")
    print(f"  Total probed:  {t['total_probed']}")
    print(f"  Total broken:  {t['total_broken']}")
    print(f"  Sites with broken: {t['sites_with_broken']}")
    print(f"\nReport: {OUTDIR / f'{TODAY}.json'}")


if __name__ == "__main__":
    asyncio.run(main())
