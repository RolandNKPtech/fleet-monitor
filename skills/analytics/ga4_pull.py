"""Pull GA4 reports into the local Parquet lake via the GA4 Data API v1beta.

Three report types are pulled per property, per day in the requested window:

  property_metrics  date x property        : top-level KPI totals
  traffic_sources   date x property x src  : sessions/users by source+medium
  top_pages         date x property x pg   : views by landingPage / pagePath

Layout: data/analytics/ga4/<report>/<property_id>/<YYYY-MM>.parquet

Modes:
  - incremental (default): rolling 7-day window
  - full: last 730 days (~2 years; GA4 has no hard API ceiling)

Dedupe across accounts: each property_id is pulled by the first account in
meta/properties.parquet that has access; account recorded inline.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .oauth import TokenSession, api_post, live_sessions

REPO_ROOT = Path(__file__).resolve().parents[2]
META_DIR = REPO_ROOT / "data" / "analytics" / "meta"
GA4_DIR = REPO_ROOT / "data" / "analytics" / "ga4"
STATE_DIR = REPO_ROOT / "data" / "analytics" / "state"

GA4_DATA = "https://analyticsdata.googleapis.com/v1beta"


# --- Report definitions ---------------------------------------------------

PROPERTY_METRICS_SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("property_id", pa.string()),
    ("sessions", pa.int64()),
    ("active_users", pa.int64()),
    ("total_users", pa.int64()),
    ("screen_page_views", pa.int64()),
    ("conversions", pa.int64()),
    ("engagement_rate", pa.float64()),
    ("avg_session_duration", pa.float64()),
    ("account", pa.string()),
    ("pulled_at", pa.timestamp("us", tz="UTC")),
])

TRAFFIC_SOURCES_SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("property_id", pa.string()),
    ("session_source", pa.string()),
    ("session_medium", pa.string()),
    ("session_default_channel_group", pa.string()),
    ("sessions", pa.int64()),
    ("active_users", pa.int64()),
    ("conversions", pa.int64()),
    ("account", pa.string()),
    ("pulled_at", pa.timestamp("us", tz="UTC")),
])

TOP_PAGES_SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("property_id", pa.string()),
    ("page_path", pa.string()),
    ("landing_page", pa.string()),
    ("screen_page_views", pa.int64()),
    ("active_users", pa.int64()),
    ("avg_session_duration", pa.float64()),
    ("account", pa.string()),
    ("pulled_at", pa.timestamp("us", tz="UTC")),
])


REPORTS = {
    "property_metrics": {
        "dimensions": ["date"],
        "metrics": [
            "sessions", "activeUsers", "totalUsers", "screenPageViews",
            "conversions", "engagementRate", "averageSessionDuration",
        ],
        "schema": PROPERTY_METRICS_SCHEMA,
    },
    "traffic_sources": {
        "dimensions": ["date", "sessionSource", "sessionMedium", "sessionDefaultChannelGroup"],
        "metrics": ["sessions", "activeUsers", "conversions"],
        "schema": TRAFFIC_SOURCES_SCHEMA,
    },
    "top_pages": {
        "dimensions": ["date", "pagePath", "landingPage"],
        "metrics": ["screenPageViews", "activeUsers", "averageSessionDuration"],
        "schema": TOP_PAGES_SCHEMA,
    },
}


# --- Helpers --------------------------------------------------------------

def load_property_assignments() -> list[tuple[str, str]]:
    """Return (property_id, account_label) — one row per unique property (first account wins)."""
    pq_path = META_DIR / "properties.parquet"
    if not pq_path.exists():
        raise FileNotFoundError(f"Run discover.py first; missing {pq_path}")
    seen: dict[str, str] = {}
    for row in pq.read_table(pq_path).to_pylist():
        pid = row["property_id"]
        if not pid or pid in seen:
            continue
        seen[pid] = row["account"]
    return list(seen.items())


def fetch_report(session: TokenSession, property_id: str, report: str,
                 start_date: str, end_date: str) -> tuple[list[dict], str | None]:
    """Run a runReport against the property; return parsed rows."""
    cfg = REPORTS[report]
    url = f"{GA4_DATA}/properties/{property_id}:runReport"
    payload = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": d} for d in cfg["dimensions"]],
        "metrics": [{"name": m} for m in cfg["metrics"]],
        "limit": 100000,
        "keepEmptyRows": False,
    }
    status, body = api_post(session, url, payload)
    if status != 200 or not isinstance(body, dict):
        err = body.get("error", {}).get("message") if isinstance(body, dict) else str(body)[:200]
        return [], f"HTTP {status}: {err}"

    dim_names = cfg["dimensions"]
    metric_names = cfg["metrics"]
    out = []
    for row in body.get("rows", []) or []:
        dim_values = [d.get("value", "") for d in row.get("dimensionValues", [])]
        metric_values = [m.get("value", "0") for m in row.get("metricValues", [])]
        dims = dict(zip(dim_names, dim_values))
        metrics = dict(zip(metric_names, metric_values))
        out.append({**dims, **metrics})
    return out, None


def parse_property_metrics(api_rows: list[dict], property_id: str, account: str, pulled_at: datetime) -> list[dict]:
    out = []
    for r in api_rows:
        try:
            d = datetime.strptime(r["date"], "%Y%m%d").date()
        except (KeyError, ValueError):
            continue
        out.append({
            "date": d,
            "property_id": property_id,
            "sessions": int(float(r.get("sessions", 0))),
            "active_users": int(float(r.get("activeUsers", 0))),
            "total_users": int(float(r.get("totalUsers", 0))),
            "screen_page_views": int(float(r.get("screenPageViews", 0))),
            "conversions": int(float(r.get("conversions", 0))),
            "engagement_rate": float(r.get("engagementRate", 0.0)),
            "avg_session_duration": float(r.get("averageSessionDuration", 0.0)),
            "account": account,
            "pulled_at": pulled_at,
        })
    return out


def parse_traffic_sources(api_rows: list[dict], property_id: str, account: str, pulled_at: datetime) -> list[dict]:
    out = []
    for r in api_rows:
        try:
            d = datetime.strptime(r["date"], "%Y%m%d").date()
        except (KeyError, ValueError):
            continue
        out.append({
            "date": d,
            "property_id": property_id,
            "session_source": r.get("sessionSource", ""),
            "session_medium": r.get("sessionMedium", ""),
            "session_default_channel_group": r.get("sessionDefaultChannelGroup", ""),
            "sessions": int(float(r.get("sessions", 0))),
            "active_users": int(float(r.get("activeUsers", 0))),
            "conversions": int(float(r.get("conversions", 0))),
            "account": account,
            "pulled_at": pulled_at,
        })
    return out


def parse_top_pages(api_rows: list[dict], property_id: str, account: str, pulled_at: datetime) -> list[dict]:
    out = []
    for r in api_rows:
        try:
            d = datetime.strptime(r["date"], "%Y%m%d").date()
        except (KeyError, ValueError):
            continue
        out.append({
            "date": d,
            "property_id": property_id,
            "page_path": r.get("pagePath", ""),
            "landing_page": r.get("landingPage", ""),
            "screen_page_views": int(float(r.get("screenPageViews", 0))),
            "active_users": int(float(r.get("activeUsers", 0))),
            "avg_session_duration": float(r.get("averageSessionDuration", 0.0)),
            "account": account,
            "pulled_at": pulled_at,
        })
    return out


PARSERS = {
    "property_metrics": parse_property_metrics,
    "traffic_sources": parse_traffic_sources,
    "top_pages": parse_top_pages,
}


def _month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def write_partitions(rows: list[dict], report: str, property_id: str) -> dict[str, int]:
    """Group by year-month, merge with existing, write."""
    if not rows:
        return {}
    schema = REPORTS[report]["schema"]
    by_month: dict[str, list[dict]] = {}
    for r in rows:
        by_month.setdefault(_month_key(r["date"]), []).append(r)

    written: dict[str, int] = {}
    out_dir = GA4_DIR / report / property_id
    out_dir.mkdir(parents=True, exist_ok=True)

    for ym, new_rows in by_month.items():
        path = out_dir / f"{ym}.parquet"
        new_dates = {r["date"] for r in new_rows}
        if path.exists():
            existing = pq.read_table(path).to_pylist()
            kept = [r for r in existing if r["date"] not in new_dates]
            merged = kept + new_rows
        else:
            merged = new_rows
        pq.write_table(pa.Table.from_pylist(merged, schema=schema), path, compression="zstd")
        written[ym] = len(merged)
    return written


# --- Orchestration --------------------------------------------------------

def run(*, mode: str = "incremental", days: int = 7,
        properties_filter: list[str] | None = None,
        reports_filter: list[str] | None = None,
        limit: int | None = None) -> dict:
    """Pull GA4 data; return summary."""
    GA4_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    end_date = (datetime.now(timezone.utc).date() - timedelta(days=1))
    if mode == "full":
        start_date = end_date - timedelta(days=730)  # 2 years
    else:
        start_date = end_date - timedelta(days=days)

    sessions_by_label = {s.label: s for s in live_sessions()}
    print(f"Live sessions: {sorted(sessions_by_label.keys())}", flush=True)
    print(f"Date range: {start_date} -> {end_date}  ({(end_date - start_date).days} days)", flush=True)

    assignments = load_property_assignments()
    if properties_filter:
        wanted = set(properties_filter)
        assignments = [a for a in assignments if a[0] in wanted]
    if limit:
        assignments = assignments[:limit]

    reports_to_run = reports_filter or list(REPORTS)
    print(f"Properties to pull: {len(assignments)}  reports: {reports_to_run}", flush=True)

    pulled_at = datetime.now(timezone.utc)
    summary = {"properties_ok": 0, "properties_failed": 0, "rows_total": 0,
               "partitions_written": 0, "report_failures": 0}
    errors: list[dict] = []

    for i, (property_id, account_label) in enumerate(assignments, 1):
        sess = sessions_by_label.get(account_label)
        if not sess:
            errors.append({"property_id": property_id, "account": account_label,
                           "report": "ALL", "error": "no live session"})
            summary["properties_failed"] += 1
            continue

        any_ok = False
        for report in reports_to_run:
            api_rows, err = fetch_report(
                sess, property_id, report, start_date.isoformat(), end_date.isoformat()
            )
            if err:
                errors.append({"property_id": property_id, "account": account_label,
                               "report": report, "error": err})
                summary["report_failures"] += 1
                continue
            parser = PARSERS[report]
            rows = parser(api_rows, property_id, account_label, pulled_at)
            written = write_partitions(rows, report, property_id)
            summary["rows_total"] += len(rows)
            summary["partitions_written"] += len(written)
            any_ok = True

        if any_ok:
            summary["properties_ok"] += 1
        else:
            summary["properties_failed"] += 1

        if i % 25 == 0 or len(assignments) < 10:
            print(f"  [{i}/{len(assignments)}] property={property_id}: rows={summary['rows_total']} partitions={summary['partitions_written']}", flush=True)

    log_path = STATE_DIR / "pull_log.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "source": "ga4",
            "mode": mode,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "started_at": pulled_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **summary,
            "error_count": len(errors),
        }) + "\n")

    if errors:
        err_path = STATE_DIR / f"ga4_errors_{pulled_at.strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        err_path.write_text("\n".join(json.dumps(e) for e in errors), encoding="utf-8")
        summary["error_file"] = str(err_path.relative_to(REPO_ROOT))

    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["incremental", "full"], default="incremental")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--property", action="append", help="filter to one or more property IDs")
    p.add_argument("--report", action="append", choices=list(REPORTS),
                   help="run only these reports (default: all)")
    p.add_argument("--limit", type=int, help="cap property count for smoke tests")
    args = p.parse_args()

    summary = run(
        mode=args.mode, days=args.days,
        properties_filter=args.property,
        reports_filter=args.report,
        limit=args.limit,
    )
    print("\nGA4 pull summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
