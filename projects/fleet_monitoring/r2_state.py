"""Cloudflare R2 state sync — make the pipeline stateless-runner-safe.

The fleet pipeline holds critical state on local disk:
  - data/snapshots/*.json      (per-day snapshot, historical)
  - data/timeseries.jsonl      (append-only daily rollup)
  - data/daily.jsonl           (per-site per-day metric rows)
  - data/run-log.jsonl         (append-only run records)
  - data/sites/*.html          (~696 generated per-site pages)
  - data/dashboard.html        (entry HTML)
  - data/console.html          (alternate console view)
  - data/fleet.db              (sqlite — interventions + effectiveness)
  - data/analytics/            (parquet lake from skills.analytics.pull)
  - data/alerts-latest.json    (current alert state for lifecycle diffing)
  - config/alltoken.json       (GA4/GSC OAuth — self-refreshes during pulls)

On a stateless runner (GitHub Actions), all of that must be pulled from
R2 before the pipeline runs and pushed back after. This module is the
round-trip.

Design:
  - pull_from_r2() runs at the start of main(), restoring local files.
  - push_to_r2() runs in the `finally:` block of main(), so even a failed
    run uploads its updated run-log entry — that's how silent token
    failures become visible. See analytics_health.evaluate_analytics_health.
  - Uses boto3 against R2's S3-compatible endpoint. R2 credentials come
    from environment variables (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
    R2_ACCOUNT_ID, R2_BUCKET). When any are missing the helpers no-op
    silently — local-only runs (developer laptop without R2 config)
    still work.

Safety:
  - Push uses `put_object` per key. A failed push doesn't corrupt prior
    objects (each key is atomic at the R2 layer).
  - We DO NOT delete remote objects that vanished locally — the pipeline
    sometimes drops files transiently (e.g. write_snapshot replacing
    today's JSON). Trust local-as-source-of-truth for content but never
    for absence.
  - Lake parquet files are uploaded with their content hash as part of
    the key — re-uploading the same partition is idempotent.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .models import DATA_DIR, PROJECT_DIR, ROOT


# Inventory: every local prefix the pipeline depends on, paired with the
# remote key prefix in R2. Local prefix is RELATIVE to the nkp-ops root.
# Each tuple: (local_path, remote_prefix, kind)
#   kind="dir" -> recursive walk + upload
#   kind="file" -> single file
_INVENTORY: tuple[tuple[Path, str, str], ...] = (
    (DATA_DIR / "snapshots", "fleet/snapshots", "dir"),
    (DATA_DIR / "sites", "fleet/sites", "dir"),
    (DATA_DIR / "timeseries.jsonl", "fleet/timeseries.jsonl", "file"),
    (DATA_DIR / "daily.jsonl", "fleet/daily.jsonl", "file"),
    (DATA_DIR / "run-log.jsonl", "fleet/run-log.jsonl", "file"),
    (DATA_DIR / "dashboard.html", "fleet/dashboard.html", "file"),
    (DATA_DIR / "console.html", "fleet/console.html", "file"),
    (DATA_DIR / "alerts-latest.json", "fleet/alerts-latest.json", "file"),
    (DATA_DIR / "roster.json", "fleet/roster.json", "file"),
    (DATA_DIR / "fleet.db", "fleet/fleet.db", "file"),
    (ROOT / "data" / "analytics", "lake/analytics", "dir"),
    # R2 health scan output. Local cron runs scripts/monitor_r2_health.py
    # daily; that script calls push_one() to upload immediately, but this
    # inventory entry ensures the dashboard pipeline also round-trips the
    # files (so a render-only run on a fresh GHA runner still has data).
    (ROOT / "data" / "reports" / "r2-health", "fleet/r2-health", "dir"),
)

# OAuth token bundle is OUTSIDE the project dir today — kept in
# builds-reference/ on the developer machine but lifted to R2 for the
# stateless runner. See skills.analytics.oauth for the canonical path.
_OAUTH_TOKEN_LOCAL = (ROOT / "builds-reference" / "cloudflare-project"
                      / "sites" / "GA4" / "alltoken.json")
_OAUTH_TOKEN_REMOTE = "auth/alltoken.json"


def _r2_config() -> dict | None:
    """Read R2 connection config from env. Returns None if any field is
    missing — caller then treats the sync as a local-only no-op.

    `ACCT` is accepted as a fallback for `R2_ACCOUNT_ID` because the
    operator's deploy env file uses that shorter name. Access/secret/bucket
    have no fallbacks — the FLEET_-prefixed variants in some .env files
    point at a DIFFERENT R2 scope (no write access to fleet-monitor) so
    accepting them would silently bypass auth and surface as AccessDenied.
    """
    access = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    account = (os.environ.get("R2_ACCOUNT_ID", "").strip()
               or os.environ.get("ACCT", "").strip())
    bucket = os.environ.get("R2_BUCKET", "").strip() or "fleet-monitor"
    if not (access and secret and account):
        return None
    return {
        "access_key": access,
        "secret_key": secret,
        "endpoint": f"https://{account}.r2.cloudflarestorage.com",
        "bucket": bucket,
    }


def _client(cfg: dict):
    """Build a boto3 S3 client pointed at R2. Imported lazily so the
    helpers don't pull boto3 into local-only runs."""
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="auto",
        config=Config(retries={"max_attempts": 3, "mode": "standard"},
                      signature_version="s3v4"),
    )


def _iter_local_files(path: Path) -> list[Path]:
    """All files under a directory, recursively. Returns [] for missing dirs."""
    if not path.exists() or not path.is_dir():
        return []
    return [p for p in path.rglob("*") if p.is_file()]


def _put_file(client, bucket: str, key: str, path: Path) -> None:
    """Upload one file to R2 at `key`. Atomic at the object layer."""
    with path.open("rb") as fh:
        client.put_object(Bucket=bucket, Key=key, Body=fh)


def push_one(local_path: Path, remote_key: str) -> bool:
    """Push a single local file to R2 at `remote_key`. Returns True on success.

    Returns False (silently) when R2 env is unset, the local file is missing,
    or the upload errors out. Designed for one-off uploaders that want to
    publish a fresh artifact (e.g. monitor_r2_health.py pushing its scan JSON)
    without invoking the full pipeline inventory walk.
    """
    cfg = _r2_config()
    if cfg is None or not local_path.exists():
        return False
    try:
        client = _client(cfg)
        _put_file(client, cfg["bucket"], remote_key, local_path)
        return True
    except Exception as e:  # pragma: no cover - boto3 surface area
        print(f"R2 push failed for {remote_key}: {e}", file=sys.stderr)
        return False


def _get_file(client, bucket: str, key: str, dest: Path) -> bool:
    """Download R2 object `key` to `dest`. Returns False on 404 (the
    expected case for first-run or never-uploaded keys). Re-raises on
    any other error so an auth failure can't masquerade as a clean start."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.download_file(bucket, key, str(dest))
        return True
    except Exception as e:
        # Match botocore's 404 case without importing the exception class
        # (keeps this module light when boto3 isn't installed).
        msg = str(e)
        if "404" in msg or "NoSuchKey" in msg or "Not Found" in msg:
            return False
        raise


def _list_keys(client, bucket: str, prefix: str) -> list[str]:
    """All keys under prefix. R2 follows S3 1000-per-page pagination."""
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents") or []:
            keys.append(obj["Key"])
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return keys


def pull_from_r2() -> dict:
    """Pull all known state from R2 into the local data tree.

    Idempotent — safe to call on a tree that already has matching files
    (download_file overwrites). Returns a `{stats}` dict for the run-log
    so an operator can see how much state was restored.

    Local-only safety: returns {"skipped": "no R2 config"} when env vars
    are absent.
    """
    cfg = _r2_config()
    if cfg is None:
        return {"skipped": "no R2 config in env"}
    started = time.monotonic()
    client = _client(cfg)
    files_pulled = 0
    bytes_pulled = 0
    errors: list[str] = []

    # Walk inventory; for each remote prefix, list and pull every key.
    for local, remote, kind in _INVENTORY:
        try:
            if kind == "file":
                ok = _get_file(client, cfg["bucket"], remote, local)
                if ok:
                    files_pulled += 1
                    bytes_pulled += local.stat().st_size
                continue
            # dir: list everything under remote prefix and place locally.
            for key in _list_keys(client, cfg["bucket"], remote + "/"):
                # Reconstruct local path: strip the remote prefix.
                rel = key[len(remote) + 1:]
                dest = local / rel
                _get_file(client, cfg["bucket"], key, dest)
                files_pulled += 1
                bytes_pulled += dest.stat().st_size
        except Exception as e:                              # pragma: no cover
            errors.append(f"{remote}: {type(e).__name__}: {e}")

    # OAuth tokens — outside the project dir, fetch separately.
    try:
        ok = _get_file(client, cfg["bucket"], _OAUTH_TOKEN_REMOTE,
                       _OAUTH_TOKEN_LOCAL)
        if ok:
            files_pulled += 1
            bytes_pulled += _OAUTH_TOKEN_LOCAL.stat().st_size
    except Exception as e:                                  # pragma: no cover
        errors.append(f"oauth: {type(e).__name__}: {e}")

    return {
        "files_pulled": files_pulled,
        "bytes_pulled": bytes_pulled,
        "duration_s": round(time.monotonic() - started, 1),
        "errors": errors,
    }


def push_to_r2() -> dict:
    """Push all known local state to R2.

    Safe to call from a `finally:` block — a failed run still uploads
    its run-log entry so the operator can see what broke. Skips silently
    when R2 env vars are absent (developer-laptop runs).

    NEVER deletes remote objects. Local-as-source-of-truth for content,
    not for absence: a transient missing file shouldn't drop history.
    """
    cfg = _r2_config()
    if cfg is None:
        return {"skipped": "no R2 config in env"}
    started = time.monotonic()
    client = _client(cfg)
    files_pushed = 0
    bytes_pushed = 0
    errors: list[str] = []

    for local, remote, kind in _INVENTORY:
        try:
            if kind == "file":
                if local.exists():
                    _put_file(client, cfg["bucket"], remote, local)
                    files_pushed += 1
                    bytes_pushed += local.stat().st_size
                continue
            for path in _iter_local_files(local):
                rel = path.relative_to(local).as_posix()
                key = f"{remote}/{rel}"
                _put_file(client, cfg["bucket"], key, path)
                files_pushed += 1
                bytes_pushed += path.stat().st_size
        except Exception as e:                              # pragma: no cover
            errors.append(f"{remote}: {type(e).__name__}: {e}")

    # OAuth tokens — push only if locally present (the SDK may have
    # refreshed them mid-run; we want the latest in R2 for next run).
    if _OAUTH_TOKEN_LOCAL.exists():
        try:
            _put_file(client, cfg["bucket"], _OAUTH_TOKEN_REMOTE,
                      _OAUTH_TOKEN_LOCAL)
            files_pushed += 1
        except Exception as e:                              # pragma: no cover
            errors.append(f"oauth: {type(e).__name__}: {e}")

    return {
        "files_pushed": files_pushed,
        "bytes_pushed": bytes_pushed,
        "duration_s": round(time.monotonic() - started, 1),
        "errors": errors,
    }


def _cli() -> int:
    """Quick CLI for diagnostics: `python -m projects.fleet_monitoring.r2_state pull|push|info`"""
    import json as _json
    if len(sys.argv) < 2:
        print("usage: pull | push | info", file=sys.stderr)
        return 2
    op = sys.argv[1]
    if op == "info":
        cfg = _r2_config()
        if cfg is None:
            print("no R2 config in env (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, "
                  "R2_ACCOUNT_ID, R2_BUCKET)")
            return 1
        print(_json.dumps({"endpoint": cfg["endpoint"],
                           "bucket": cfg["bucket"]}, indent=2))
        return 0
    if op == "pull":
        print(_json.dumps(pull_from_r2(), indent=2))
        return 0
    if op == "push":
        print(_json.dumps(push_to_r2(), indent=2))
        return 0
    print(f"unknown op: {op}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
