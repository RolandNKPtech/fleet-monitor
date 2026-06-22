import asyncio
import json
from pathlib import Path

import projects.fleet_monitoring.serve as serve_mod


def _write_snapshot(dir_path: Path, body: dict) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / f"{body['date']}.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def test_refresh_site_returns_400_when_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(serve_mod, "SNAPSHOTS_DIR", tmp_path)
    code, body = serve_mod.handle_refresh_site_request(body_bytes=b"{}")
    assert code == 400
    assert "missing" in body["error"].lower()


def test_refresh_site_returns_404_when_site_not_in_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(serve_mod, "SNAPSHOTS_DIR", tmp_path)
    _write_snapshot(tmp_path, {"date": "2026-05-19", "sites": [{"key": "a.com"}]})
    code, body = serve_mod.handle_refresh_site_request(
        body_bytes=json.dumps({"key": "nope.com"}).encode())
    assert code == 404
    assert "not in" in body["error"].lower()


def test_refresh_site_short_circuits_when_site_has_no_cf_zone(tmp_path, monkeypatch):
    monkeypatch.setattr(serve_mod, "SNAPSHOTS_DIR", tmp_path)
    _write_snapshot(tmp_path, {"date": "2026-05-19", "sites": [
        {"key": "noccf.com", "wpe": {}, "cf": None}]})
    code, body = serve_mod.handle_refresh_site_request(
        body_bytes=json.dumps({"key": "noccf.com"}).encode())
    assert code == 200
    assert "no CF zone" in body.get("note", "")


def test_refresh_site_happy_path_patches_snapshot_and_returns_fetched_at(
        tmp_path, monkeypatch):
    """Patches the CF client to return fake per_site data, then asserts
    snapshot file on disk gets the new per_site block."""
    sites_dir = tmp_path / "sites"
    monkeypatch.setattr(serve_mod, "SNAPSHOTS_DIR", tmp_path)
    import projects.fleet_monitoring.render_site as rs
    monkeypatch.setattr(rs, "SITES_DIR", sites_dir)

    snap = {"date": "2026-05-19", "captured_at": "2026-05-19T22:00:00+00:00",
            "alerts": [], "sites": [{
                "key": "a.com", "apex": "a.com", "join_state": "wpe+cf",
                "wpe": {"install": "x", "account_name": "acctA",
                        "bandwidth_gb_30d": 10, "billable_visits_30d": 100,
                        "mb_per_visit": 100, "storage_gb": 1},
                "cf": {"zone_id": "z1", "config": {"settings": {}},
                       "analytics": {"requests_30d": 100, "threats": 0,
                                     "cache_hit_rate": 50}},
                "alerts_count": 0}]}
    snap_path = _write_snapshot(tmp_path, snap)

    async def fake_fetch_all(client, zone_id):
        return {"fetched_at": "2026-05-19T23:00:00+00:00",
                "country_window_days": 30, "traffic_window_days": 7,
                "total_requests_7d": 700,
                "countries": [{"country": "US", "requests": 100, "bytes": 500}],
                "requests_threats_daily": [], "top_paths": [], "top_uas": []}

    import projects.fleet_monitoring.cf_per_site as cps
    monkeypatch.setattr(cps, "fetch_all_for_zone", fake_fetch_all)
    monkeypatch.setattr(serve_mod, "_get_cf_client", lambda: object())

    code, body = serve_mod.handle_refresh_site_request(
        body_bytes=json.dumps({"key": "a.com"}).encode())
    assert code == 200
    assert body["key"] == "a.com"
    assert body["fetched_at"] == "2026-05-19T23:00:00+00:00"
    patched = json.loads(snap_path.read_text(encoding="utf-8"))
    assert patched["sites"][0]["cf"]["per_site"]["countries"][0]["country"] == "US"
    assert (sites_dir / "a.com.html").exists()


def test_console_html_served(tmp_path, monkeypatch):
    """serve.py exposes a CONSOLE_FILE module attribute pointing at the file."""
    import projects.fleet_monitoring.serve as serve_mod
    console = tmp_path / "console.html"
    console.write_text("<!DOCTYPE html><html>console</html>", encoding="utf-8")
    monkeypatch.setattr(serve_mod, "CONSOLE_FILE", console)
    assert serve_mod.CONSOLE_FILE.read_text(encoding="utf-8").startswith("<!DOCTYPE")
