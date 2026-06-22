"""Pull GSC Search Analytics data into the local Parquet lake.

Layout: data/analytics/gsc/search_analytics/<safe_host>/<YYYY-MM>.parquet

Plain directory hierarchy (no Hive `key=` prefix) so pyarrow's partition
discovery cannot inject a phantom `host` column on read. The `host` column
lives inline in every row.

Schema per row:
  date, site_url, host, query, page, country, device,
  clicks, impressions, ctr, position, account, pulled_at

Modes:
  - incremental (default): rolling 7-day window, refetches recent data
  - full: 16 months back to the API limit (~480 days)

Dedupe across accounts: each host is fetched via the first account in
meta/sites.parquet that has access; the source account is recorded in the row.
"""

from __future__ import annotations

import argparse
import json
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .oauth import TokenSession, api_post, live_sessions

REPO_ROOT = Path(__file__).resolve().parents[2]
META_DIR = REPO_ROOT / "data" / "analytics" / "meta"
GSC_DIR = REPO_ROOT / "data" / "analytics" / "gsc" / "search_analytics"
STATE_DIR = REPO_ROOT / "data" / "analytics" / "state"

GSC = "https://searchconsole.googleapis.com/webmasters/v3"
GSC_DIMS = ["date", "query", "page", "country", "device"]
GSC_PAGE_LIMIT = 25000  # API max per request

GSC_SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("site_url", pa.string()),
    ("host", pa.string()),
    ("query", pa.string()),
    ("page", pa.string()),
    ("country", pa.string()),
    ("device", pa.string()),
    ("clicks", pa.int64()),
    ("impressions", pa.int64()),
    ("ctr", pa.float64()),
    ("position", pa.float64()),
    ("account", pa.string()),
    ("pulled_at", pa.timestamp("us", tz="UTC")),
])


def _safe_host_dirname(host: str) -> str:
    """Filesystem-safe directory name for a host (drop port, replace any oddities)."""
    return host.replace(":", "_").replace("/", "_")


def load_site_assignments() -> list[tuple[str, str, str]]:
    """Return (host, site_url, account) tuples — one per unique host (first account wins)."""
    sites_pq = META_DIR / "sites.parquet"
    if not sites_pq.exists():
        raise FileNotFoundError(f"Run discover.py first; missing {sites_pq}")
    tbl = pq.read_table(sites_pq).to_pylist()
    seen: dict[str, tuple[str, str, str]] = {}
    for row in tbl:
        host = row["host"]
        if not host or host in seen:
            continue
        seen[host] = (host, row["site_url"], row["account"])
    return list(seen.values())


def fetch_chunk(session: TokenSession, site_url: str, start_date: str, end_date: str,
                lock: threading.Lock | None = None) -> tuple[list[dict], str | None]:
    """Fetch one chunk of rows for site over [start_date, end_date]. Paginates if needed.

    Pass `lock` to serialize OAuth refresh calls when sharing a session across threads.
    """
    url = f"{GSC}/sites/{urllib.parse.quote(site_url, safe='')}/searchAnalytics/query"
    all_rows: list[dict] = []
    start_row = 0
    while True:
        payload = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": GSC_DIMS,
            "rowLimit": GSC_PAGE_LIMIT,
            "startRow": start_row,
            "type": "web",
        }
        if lock is not None:
            with lock:
                session.ensure_fresh()
        status, body = api_post(session, url, payload)
        if status != 200 or not isinstance(body, dict):
            err = body.get("error", {}).get("message") if isinstance(body, dict) else str(body)[:200]
            return all_rows, f"HTTP {status}: {err}"
        rows = body.get("rows", []) or []
        all_rows.extend(rows)
        if len(rows) < GSC_PAGE_LIMIT:
            return all_rows, None
        start_row += GSC_PAGE_LIMIT
        if start_row > GSC_PAGE_LIMIT * 20:  # 500k rows in a single 30-day chunk = unusual
            return all_rows, f"pagination cap reached at {start_row}"


def fetch_site(session: TokenSession, site_url: str, start_date: date, end_date: date,
               chunk_days: int = 30, lock: threading.Lock | None = None
               ) -> tuple[list[dict], list[str]]:
    """Fetch rows over the full date range by stepping through chunks of `chunk_days`.

    Returns (rows, errors) — errors is a list of per-chunk error messages (may be empty).
    """
    all_rows: list[dict] = []
    errors: list[str] = []
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end_date)
        rows, err = fetch_chunk(session, site_url, cursor.isoformat(), chunk_end.isoformat(), lock=lock)
        if err:
            errors.append(f"{cursor.isoformat()}..{chunk_end.isoformat()}: {err}")
        else:
            all_rows.extend(rows)
        cursor = chunk_end + timedelta(days=1)
    return all_rows, errors


def parse_rows(api_rows: list[dict], site_url: str, host: str, account: str, pulled_at: datetime) -> list[dict]:
    """Convert GSC API row shape into our flat schema."""
    out = []
    for r in api_rows:
        keys = r.get("keys", [])
        if len(keys) != len(GSC_DIMS):
            continue
        date_str, query, page, country, device = keys
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        out.append({
            "date": date_obj,
            "site_url": site_url,
            "host": host,
            "query": query,
            "page": page,
            "country": country,
            "device": device,
            "clicks": int(r.get("clicks", 0) or 0),
            "impressions": int(r.get("impressions", 0) or 0),
            "ctr": float(r.get("ctr", 0.0) or 0.0),
            "position": float(r.get("position", 0.0) or 0.0),
            "account": account,
            "pulled_at": pulled_at,
        })
    return out


def _month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def write_partitions(rows: list[dict], host: str) -> dict[str, int]:
    """Group rows by year-month, merge with existing partition (overwriting affected dates), write."""
    if not rows:
        return {}
    by_month: dict[str, list[dict]] = {}
    for r in rows:
        by_month.setdefault(_month_key(r["date"]), []).append(r)

    written: dict[str, int] = {}
    host_dir = GSC_DIR / _safe_host_dirname(host)
    host_dir.mkdir(parents=True, exist_ok=True)

    for ym, new_rows in by_month.items():
        path = host_dir / f"{ym}.parquet"
        new_dates = {r["date"] for r in new_rows}

        if path.exists():
            existing = pq.read_table(path).to_pylist()
            kept = [r for r in existing if r["date"] not in new_dates]
            merged = kept + new_rows
        else:
            merged = new_rows

        pq.write_table(pa.Table.from_pylist(merged, schema=GSC_SCHEMA), path, compression="zstd")
        written[ym] = len(merged)
    return written


def _pull_one_site(host: str, site_url: str, account_label: str, session: TokenSession,
                   start_date: date, end_date: date, chunk_days: int, pulled_at: datetime,
                   refresh_lock: threading.Lock) -> dict:
    """Worker function: pull one site and write partitions. Returns a status dict."""
    api_rows, chunk_errors = fetch_site(
        session, site_url, start_date, end_date, chunk_days=chunk_days, lock=refresh_lock,
    )
    rows = parse_rows(api_rows, site_url, host, account_label, pulled_at)
    written = write_partitions(rows, host)
    return {
        "host": host,
        "account": account_label,
        "rows": len(rows),
        "partitions": len(written),
        "errors": chunk_errors,
    }


def run(*, mode: str = "incremental", days: int = 7, hosts_filter: list[str] | None = None,
        limit: int | None = None, workers: int = 4, chunk_days: int = 30) -> dict:
    """Pull GSC data; return summary."""
    GSC_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    end_date = (datetime.now(timezone.utc).date() - timedelta(days=1))  # yesterday (GSC lag)
    if mode == "full":
        start_date = end_date - timedelta(days=480)  # ~16 months, GSC API max
    else:
        start_date = end_date - timedelta(days=days)

    sessions_by_label = {s.label: s for s in live_sessions()}
    print(f"Live sessions: {sorted(sessions_by_label.keys())}", flush=True)
    print(f"Date range: {start_date} -> {end_date}  ({(end_date - start_date).days} days)  chunk_days={chunk_days}  workers={workers}", flush=True)

    assignments = load_site_assignments()
    if hosts_filter:
        wanted = {h.lower() for h in hosts_filter}
        assignments = [a for a in assignments if a[0] in wanted]
    if limit:
        assignments = assignments[:limit]

    print(f"Sites to pull: {len(assignments)}", flush=True)

    pulled_at = datetime.now(timezone.utc)
    summary = {"sites_ok": 0, "sites_failed": 0, "rows_total": 0, "partitions_written": 0}
    errors: list[dict] = []
    refresh_lock = threading.Lock()

    # Skip assignments whose account has no live session before submitting work.
    valid: list[tuple[str, str, str, TokenSession]] = []
    for host, site_url, account_label in assignments:
        sess = sessions_by_label.get(account_label)
        if not sess:
            errors.append({"host": host, "account": account_label, "error": "no live session"})
            summary["sites_failed"] += 1
            continue
        valid.append((host, site_url, account_label, sess))

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_pull_one_site, h, u, a, s, start_date, end_date, chunk_days, pulled_at, refresh_lock): h
            for (h, u, a, s) in valid
        }
        for fut in as_completed(futures):
            completed += 1
            try:
                result = fut.result()
            except Exception as e:  # noqa: BLE001
                host = futures[fut]
                errors.append({"host": host, "error": f"worker exception: {e}"})
                summary["sites_failed"] += 1
                continue

            if result["errors"]:
                for em in result["errors"]:
                    errors.append({"host": result["host"], "account": result["account"], "error": em})

            summary["sites_ok"] += 1
            summary["rows_total"] += result["rows"]
            summary["partitions_written"] += result["partitions"]

            if completed % 25 == 0 or completed == len(valid) or len(valid) < 20:
                print(f"  [{completed}/{len(valid)}] {result['host']}: {result['rows']} rows  (total rows: {summary['rows_total']:,})", flush=True)

    # Log run summary
    log_path = STATE_DIR / "pull_log.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": "gsc",
            "mode": mode,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "started_at": pulled_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **summary,
            "error_count": len(errors),
        }) + "\n")

    if errors:
        err_path = STATE_DIR / f"gsc_errors_{pulled_at.strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        err_path.write_text("\n".join(json.dumps(e) for e in errors), encoding="utf-8")
        summary["error_file"] = str(err_path.relative_to(REPO_ROOT))

    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["incremental", "full"], default="incremental")
    p.add_argument("--days", type=int, default=7, help="incremental window size in days")
    p.add_argument("--host", action="append", help="filter to one or more hosts (can repeat)")
    p.add_argument("--limit", type=int, help="cap site count for smoke tests")
    p.add_argument("--workers", type=int, default=4, help="parallel site workers (default 4)")
    p.add_argument("--chunk-days", type=int, default=30, help="date chunk size in days (default 30)")
    args = p.parse_args()

    summary = run(mode=args.mode, days=args.days, hosts_filter=args.host, limit=args.limit,
                  workers=args.workers, chunk_days=args.chunk_days)
    print("\nGSC pull summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
