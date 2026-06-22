"""Orchestrator — runs discovery, then GSC and/or GA4 pulls.

Default behavior (no flags): discovery + incremental (7-day) pull for both sources.
Pass --backfill to do the full 16-month GSC / 24-month GA4 window.

Examples:
  python -m skills.analytics.pull                          # discovery + incremental both
  python -m skills.analytics.pull --source gsc             # incremental GSC only
  python -m skills.analytics.pull --backfill --source gsc  # full GSC backfill
  python -m skills.analytics.pull --source ga4 --days 30   # 30-day GA4 incremental
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from . import discover, gsc_pull, ga4_pull


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["gsc", "ga4", "all"], default="all")
    p.add_argument("--backfill", action="store_true", help="full mode (16 mo GSC, 2 yr GA4)")
    p.add_argument("--days", type=int, default=7, help="incremental window")
    p.add_argument("--workers", type=int, default=4, help="GSC parallel workers")
    p.add_argument("--skip-discover", action="store_true")
    args = p.parse_args()

    started = datetime.now(timezone.utc)

    if not args.skip_discover:
        print("=== Discovery ===", flush=True)
        disc = discover.run()
        for k, v in disc.items():
            print(f"  {k}: {v}")

    mode = "full" if args.backfill else "incremental"

    if args.source in ("gsc", "all"):
        print(f"\n=== GSC pull ({mode}) ===", flush=True)
        s = gsc_pull.run(mode=mode, days=args.days, workers=args.workers)
        for k, v in s.items():
            print(f"  {k}: {v}")

    if args.source in ("ga4", "all"):
        print(f"\n=== GA4 pull ({mode}) ===", flush=True)
        s = ga4_pull.run(mode=mode, days=args.days)
        for k, v in s.items():
            print(f"  {k}: {v}")

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"\nTotal elapsed: {elapsed:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
