"""Daily scheduler — runs run.py at $DAILY_RUN_TIME (UTC), forever.

Sidecar for the Docker compose stack. Set DAILY_RUN_TIME to "HH:MM" (24-hour,
UTC). Defaults to "22:00" UTC = 06:00 Manila / UTC+8.

When running OUTSIDE Docker on Windows, prefer the Windows Task Scheduler
entry registered in the README ("schtasks ... FleetMonitoring") instead of
this loop — it's lighter and survives reboots without keeping a process alive.
"""
from __future__ import annotations

# Script-mode bootstrap (mirrors run.py / serve.py) so both invocations work.
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    __package__ = "projects.fleet_monitoring"

# Load .env so the spawned `run.py` inherits the API creds. The docker-compose
# env_file already injects them; this is a safety net for local invocation.
from pathlib import Path as _RootPath
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(_RootPath(__file__).resolve().parents[2] / ".env")

import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

DEFAULT_TIME = "22:00"   # UTC


def _parse_time(s: str) -> tuple[int, int]:
    """Parse 'HH:MM' (24-hour). Returns (hour, minute). Raises ValueError otherwise."""
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError(f"DAILY_RUN_TIME must be HH:MM, got {s!r}")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"DAILY_RUN_TIME out of range, got {s!r}")
    return hh, mm


def _next_fire(now: datetime, hh: int, mm: int) -> datetime:
    """Datetime of the next occurrence of HH:MM UTC after `now`."""
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def main() -> None:
    raw = os.environ.get("DAILY_RUN_TIME", DEFAULT_TIME)
    try:
        hh, mm = _parse_time(raw)
    except ValueError as e:
        print(f"cron: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"cron: armed for {hh:02d}:{mm:02d} UTC daily", flush=True)
    while True:
        now = datetime.now(timezone.utc)
        target = _next_fire(now, hh, mm)
        wait_s = (target - now).total_seconds()
        print(f"cron: sleeping {wait_s/3600:.2f}h until {target.isoformat()}",
              flush=True)
        time.sleep(wait_s)
        started = datetime.now(timezone.utc)
        print(f"cron: firing pipeline at {started.isoformat()}", flush=True)
        r = subprocess.run(
            [sys.executable, "-m", "projects.fleet_monitoring.run", "--no-probes"])
        print(f"cron: pipeline exit code {r.returncode}", flush=True)


if __name__ == "__main__":
    main()
