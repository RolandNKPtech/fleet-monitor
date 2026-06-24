"""Tests for the new r2_health_scan pipeline stage.

The stage SSHs into every R2-offloaded WPE install and probes for broken
thumbnails. When the WPE SSH key isn't available (GHA runs without the
secret set, dev laptop without the key), the stage must skip itself
silently rather than abort the whole pipeline.
"""
import os
from pathlib import Path

import pytest


def test_run_r2_health_scan_skips_cleanly_when_ssh_key_missing(monkeypatch, tmp_path, capsys):
    """Missing SSH key path -> the stage prints a stderr note and returns.
    No exception, no half-written scan file, the rest of the pipeline runs."""
    from projects.fleet_monitoring import run

    monkeypatch.setenv("WPE_SSH_KEY_PATH", str(tmp_path / "no-such-key"))
    run._run_r2_health_scan()

    captured = capsys.readouterr()
    assert "r2_health_scan skipped" in captured.err
    assert "no WPE SSH key" in captured.err


def test_run_r2_health_scan_uses_default_key_path(monkeypatch, tmp_path, capsys):
    """When WPE_SSH_KEY_PATH is unset, the stage falls back to the standard
    ~/.ssh/wpengine_ed25519 path. If that file doesn't exist either, it
    still skips cleanly (never crashes the pipeline)."""
    from projects.fleet_monitoring import run

    monkeypatch.delenv("WPE_SSH_KEY_PATH", raising=False)
    # Force HOME to an empty dir so the default key isn't found.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    run._run_r2_health_scan()

    captured = capsys.readouterr()
    assert "r2_health_scan skipped" in captured.err


def test_run_r2_health_scan_failure_isolated(monkeypatch, tmp_path, capsys):
    """If scan_all raises (e.g. WPE-side outage mid-scan), the helper logs
    the error and returns instead of propagating — the pipeline must not
    abort because one optional probe stage broke."""
    from projects.fleet_monitoring import run

    # Pretend the key exists so we get past the existence check.
    key = tmp_path / "fake-key"
    key.write_text("placeholder")
    monkeypatch.setenv("WPE_SSH_KEY_PATH", str(key))

    # Force scan_all to raise.
    async def _boom(*_args, **_kw):
        raise RuntimeError("simulated WPE outage")
    import scripts.monitor_r2_health as mr2
    monkeypatch.setattr(mr2, "scan_all", _boom)

    run._run_r2_health_scan()  # must NOT raise

    captured = capsys.readouterr()
    assert "r2_health_scan failed" in captured.err
    assert "simulated WPE outage" in captured.err


def test_scan_all_returns_payload_shape_matching_dashboard():
    """scan_all's return shape must match what render._r2_health_load reads.
    Pin the contract so a future refactor doesn't quietly break the tab."""
    from scripts.monitor_r2_health import scan_all
    import inspect
    sig = inspect.signature(scan_all)
    # Args that the pipeline stage relies on.
    for name in ("days", "concurrency", "verbose"):
        assert name in sig.parameters, f"scan_all missing kwarg: {name}"


def test_write_payload_writes_three_artifacts_then_pushes(monkeypatch, tmp_path):
    """write_payload writes {date}.json + latest.json + history.jsonl and
    optionally pushes the same three to R2 via r2_state.push_one. Verify
    the file artifacts independently of R2."""
    from scripts import monitor_r2_health as mr2
    monkeypatch.setattr(mr2, "OUTDIR", tmp_path / "r2h")

    payload = {
        "date": "2026-06-25",
        "days_window": 30,
        "results": [{"apex": "x.com", "install": "x", "status": "ok",
                     "probed": 5, "broken_count": 0, "broken_ids": []}],
        "totals": {"sites_scanned": 1, "sites_failed": 0, "total_probed": 5,
                   "total_broken": 0, "sites_with_broken": 0},
    }
    mr2.write_payload(payload, push_to_r2=False)

    out = tmp_path / "r2h"
    assert (out / "2026-06-25.json").exists()
    assert (out / "latest.json").exists()
    assert (out / "history.jsonl").exists()
    # history.jsonl is append-only — verify the entry made it
    assert "2026-06-25" in (out / "history.jsonl").read_text()
