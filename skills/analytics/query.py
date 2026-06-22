"""Convenience entrypoint for DuckDB queries against the analytics data lake.

Usage:
  python -m skills.analytics.query                              # interactive shell
  python -m skills.analytics.query --sql "SELECT ..."          # one-shot SQL
  python -m skills.analytics.query --views                     # (re)load views.sql
  python -m skills.analytics.query --report top_queries_28d   # canned report
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWS_SQL = Path(__file__).with_name("views.sql")
DB_PATH = REPO_ROOT / "data" / "analytics" / "views.duckdb"

REPORTS = {
    "top_queries_28d":
        "SELECT * FROM gsc_top_queries_28d LIMIT 50",
    "wow_clicks":
        "SELECT * FROM gsc_wow_clicks WHERE clicks_last_7d > 10 LIMIT 50",
    "site_daily":
        "SELECT * FROM gsc_daily_site_totals ORDER BY host, date DESC LIMIT 100",
    "token_health":
        "SELECT * FROM meta_token_health",
    "site_count":
        "SELECT count(DISTINCT host) AS unique_hosts, count(*) AS grants FROM meta_sites",
    "lake_size":
        """
        SELECT 'gsc' AS source,
               count(DISTINCT host)    AS unique_keys,
               count(DISTINCT date)    AS unique_dates,
               count(*)                AS rows,
               min(date)               AS earliest,
               max(date)               AS latest
        FROM gsc_search_analytics
        """,
}


def open_db(*, init_views: bool = True) -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute("SET memory_limit='2GB'")
    if init_views and VIEWS_SQL.exists():
        con.execute(VIEWS_SQL.read_text())
    return con


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sql", help="run one SQL statement and print result")
    p.add_argument("--report", choices=sorted(REPORTS), help="run a canned report")
    p.add_argument("--views", action="store_true", help="(re)load views.sql and exit")
    args = p.parse_args()

    con = open_db()

    if args.views:
        print(f"Loaded views from {VIEWS_SQL}")
        return 0

    if args.report:
        sql = REPORTS[args.report]
        print(f"-- {args.report}\n{sql}\n")
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        print(con.execute(sql).fetchdf().to_string())
        return 0

    if args.sql:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        print(con.execute(args.sql).fetchdf().to_string())
        return 0

    print("Open DuckDB shell:")
    print(f"  duckdb {DB_PATH}")
    print(f"Views loaded. Try:\n  python -m skills.analytics.query --report top_queries_28d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
