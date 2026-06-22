"""Tiny HTTP server so the Fleet Monitoring dashboard has a "Refresh data" button.

Serves dashboard.html on http://127.0.0.1:8765/ and exposes two endpoints:

    POST /refresh   Kick off run.py in a background thread (idempotent while
                    a run is in flight). Returns immediately.
    GET  /status    JSON: {state, started_at, finished_at, message}.
                    state in {idle, running, completed, error}.

The dashboard's "Refresh" button calls /refresh, polls /status, then reloads
when the run finishes. The button does nothing useful when the dashboard is
opened via file:// — open via http://localhost:8765/ to actually refresh.

Stdlib only — no Flask, no dependencies.

Run with:
    python -m projects.fleet_monitoring.serve            # default port 8765
    python -m projects.fleet_monitoring.serve --port 9000
"""
from __future__ import annotations

# Script-mode bootstrap (mirrors run.py) so both invocations work:
if __package__ in (None, ""):
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    __package__ = "projects.fleet_monitoring"

# Load .env BEFORE the worker subprocess inherits the env.
from pathlib import Path as _RootPath
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(_RootPath(__file__).resolve().parents[2] / ".env")

import argparse
import asyncio
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
from datetime import datetime, timezone

from .models import CONSOLE_FILE, DASHBOARD_FILE, ROOT, SNAPSHOTS_DIR
from .render_site import render_site_page, write_all_site_pages
from . import cf_per_site
from .timeseries import read_all as _ts_read_all


def _get_cf_client():
    """Lazy import so tests can monkeypatch without the real client."""
    from skills.cloudflare.client import get_cf_client
    return get_cf_client()


def _latest_snapshot_path():
    snaps = sorted(SNAPSHOTS_DIR.glob("*.json"))
    return snaps[-1] if snaps else None


def handle_refresh_site_request(body_bytes: bytes) -> tuple[int, dict]:
    """Pure handler — parses body, runs CF queries, writes snapshot, returns (code, json).

    Separated from the HTTP layer so it's easy to test without sockets.
    """
    try:
        body = json.loads(body_bytes or b"{}")
    except json.JSONDecodeError:
        return 400, {"error": "invalid JSON body"}
    key = body.get("key")
    if not key:
        return 400, {"error": "missing 'key' in body"}

    snap_path = _latest_snapshot_path()
    if not snap_path:
        return 404, {"error": "no snapshots yet"}
    snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    site = next((s for s in snapshot.get("sites", []) if s.get("key") == key), None)
    if not site:
        return 404, {"error": f"site '{key}' not in latest snapshot"}

    zone_id = ((site.get("cf") or {}).get("zone_id"))
    if not zone_id:
        return 200, {"ok": True, "key": key,
                     "note": "no CF zone for this site — nothing to refresh"}

    client = _get_cf_client()
    try:
        per_site = asyncio.run(cf_per_site.fetch_all_for_zone(client, zone_id))
    except Exception as e:
        return 500, {"error": f"CF API failure: {e}"}

    cf = site.setdefault("cf", {"zone_id": zone_id, "config": {}, "analytics": {}})
    cf["per_site"] = per_site
    snap_path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")

    # Re-render this one page only
    html = render_site_page(site, snapshot, _ts_read_all())
    from .render_site import SITES_DIR as _SD, safe_key as _sk
    _SD.mkdir(parents=True, exist_ok=True)
    (_SD / f"{_sk(key)}.html").write_text(html, encoding="utf-8")

    return 200, {"ok": True, "key": key, "fetched_at": per_site.get("fetched_at")}

# Default to loopback for local Windows use; the Docker image overrides via
# LISTEN_HOST=0.0.0.0 so the port maps out of the container correctly.
HOST = os.environ.get("LISTEN_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("LISTEN_PORT", "8765"))

_state: dict = {"state": "idle", "started_at": None, "finished_at": None, "message": ""}
_lock = threading.Lock()


def _run_pipeline_bg() -> None:
    """Spawn a worker thread that runs the pipeline. Idempotent while running."""
    with _lock:
        if _state["state"] == "running":
            return
        _state.update({
            "state": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "message": "",
        })

    def _worker():
        try:
            cmd = [sys.executable, "-m", "projects.fleet_monitoring.run", "--no-probes"]
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            with _lock:
                if r.returncode == 0:
                    last_line = (r.stdout.strip().splitlines() or ["ok"])[-1]
                    _state.update({
                        "state": "completed",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "message": last_line,
                    })
                else:
                    _state.update({
                        "state": "error",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "message": (r.stderr or r.stdout)[-500:],
                    })
        except Exception as e:                            # pragma: no cover
            with _lock:
                _state.update({
                    "state": "error",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "message": str(e),
                })

    threading.Thread(target=_worker, daemon=True).start()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args, **kw):                   # quiet access log
        pass

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                     # noqa: N802
        if self.path in ("/", "/dashboard.html"):
            try:
                content = DASHBOARD_FILE.read_bytes()
            except FileNotFoundError:
                self.send_error(404, "dashboard.html not found — run the pipeline first")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/console.html":
            try:
                content = CONSOLE_FILE.read_bytes()
            except FileNotFoundError:
                self.send_error(404, "console.html not found — run the pipeline first")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/status":
            with _lock:
                self._send_json(200, dict(_state))
        elif self.path == "/pipeline":
            from .render_pipeline import render_pipeline_page, read_run_log
            html = render_pipeline_page(read_run_log()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif self.path.startswith("/sites/") and self.path.endswith(".html"):
            from .render_site import SITES_DIR as _SD
            fname = self.path[len("/sites/"):]
            target = (_SD / fname).resolve()
            try:
                target.relative_to(_SD.resolve())
            except ValueError:
                self.send_error(403, "path traversal blocked"); return
            try:
                content = target.read_bytes()
            except FileNotFoundError:
                self.send_error(404, f"{fname} not found — run the pipeline first"); return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def do_POST(self):                                    # noqa: N802
        if self.path == "/refresh":
            _run_pipeline_bg()
            self._send_json(202, {"started": True})
        elif self.path == "/refresh-site":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            code, body = handle_refresh_site_request(raw)
            self._send_json(code, body)
        else:
            self.send_error(404)


def main() -> None:
    p = argparse.ArgumentParser(description="Fleet monitoring dashboard server")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"Port to listen on (default {DEFAULT_PORT})")
    args = p.parse_args()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, args.port), _Handler) as srv:
        print(f"Fleet Monitoring dashboard served at http://{HOST}:{args.port}/")
        print("Open that URL in a browser. Ctrl+C to stop.")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping.")


if __name__ == "__main__":
    main()
