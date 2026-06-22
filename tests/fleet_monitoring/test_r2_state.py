"""Tests for r2_state — pull/push round-trip via a mocked S3 client.

These tests exercise the inventory walk + client wiring without
requiring a real R2 bucket. The mock implements just enough of the
boto3 S3 client surface to round-trip files in-memory.
"""
import io
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from projects.fleet_monitoring import r2_state


# ---------- mock S3 client backing in-memory storage ----------

class _MockS3Error(Exception):
    """Surface non-404 errors that mimic botocore exception strings."""


class _MockS3:
    """In-memory stand-in for the boto3 S3 client used by r2_state."""

    def __init__(self):
        self.store: dict[tuple[str, str], bytes] = {}   # (bucket, key) -> bytes

    def put_object(self, Bucket, Key, Body):
        # Body may be a file-like object — read it whole.
        if hasattr(Body, "read"):
            self.store[(Bucket, Key)] = Body.read()
        else:
            self.store[(Bucket, Key)] = bytes(Body)

    def download_file(self, Bucket, Key, dest):
        if (Bucket, Key) not in self.store:
            # boto3 raises ClientError with message containing "404"
            raise Exception(f"404 Not Found: {Key}")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.store[(Bucket, Key)])

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for (b, k) in self.store if b == Bucket and k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}


@pytest.fixture
def mock_r2(monkeypatch):
    """Inject mock S3 client + R2 env vars. Returns the mock so tests
    can inspect what landed in the in-memory store."""
    mock_client = _MockS3()
    monkeypatch.setattr(r2_state, "_client", lambda cfg: mock_client)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("R2_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("R2_BUCKET", "test-bucket")
    return mock_client


@pytest.fixture
def isolated_inventory(monkeypatch, tmp_path):
    """Redirect the inventory to a tmp tree so tests don't touch real
    data/. Returns (root, inventory)."""
    root = tmp_path / "ops"
    data = root / "data"
    fleet_data = root / "projects" / "fleet_monitoring" / "data"
    data.mkdir(parents=True)
    fleet_data.mkdir(parents=True)
    # Re-point the inventory + oauth path to the tmp tree.
    new_inventory = (
        (fleet_data / "snapshots", "fleet/snapshots", "dir"),
        (fleet_data / "sites", "fleet/sites", "dir"),
        (fleet_data / "timeseries.jsonl", "fleet/timeseries.jsonl", "file"),
        (fleet_data / "run-log.jsonl", "fleet/run-log.jsonl", "file"),
        (fleet_data / "dashboard.html", "fleet/dashboard.html", "file"),
        (data / "analytics", "lake/analytics", "dir"),
    )
    oauth_local = root / "auth" / "alltoken.json"
    monkeypatch.setattr(r2_state, "_INVENTORY", new_inventory)
    monkeypatch.setattr(r2_state, "_OAUTH_TOKEN_LOCAL", oauth_local)
    return root, new_inventory


# ---------- tests ----------

def test_pull_skips_when_r2_env_missing(monkeypatch):
    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
              "R2_ACCOUNT_ID", "R2_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    out = r2_state.pull_from_r2()
    assert out == {"skipped": "no R2 config in env"}


def test_push_skips_when_r2_env_missing(monkeypatch):
    for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
              "R2_ACCOUNT_ID", "R2_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    out = r2_state.push_to_r2()
    assert out == {"skipped": "no R2 config in env"}


def test_push_uploads_files_and_dirs(mock_r2, isolated_inventory):
    root, inv = isolated_inventory
    # Lay down sample files matching the inventory shapes.
    def _w(p: Path, body: str):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)

    _w(root / "projects/fleet_monitoring/data/snapshots/2026-06-10.json",
       '{"date": "2026-06-10"}')
    _w(root / "projects/fleet_monitoring/data/snapshots/2026-06-11.json",
       '{"date": "2026-06-11"}')
    _w(root / "projects/fleet_monitoring/data/sites/example-com.html",
       "<html>site</html>")
    _w(root / "projects/fleet_monitoring/data/timeseries.jsonl",
       '{"row": 1}\n')
    _w(root / "projects/fleet_monitoring/data/dashboard.html",
       "<html>dashboard</html>")

    out = r2_state.push_to_r2()
    # 5 files: 2 snapshots + 1 site + 1 jsonl + 1 dashboard
    assert out["files_pushed"] == 5
    assert out["errors"] == []
    assert out["bytes_pushed"] > 0
    # Keys are namespaced under the remote prefixes.
    keys = {k for (_, k) in mock_r2.store.keys()}
    assert "fleet/snapshots/2026-06-10.json" in keys
    assert "fleet/snapshots/2026-06-11.json" in keys
    assert "fleet/sites/example-com.html" in keys
    assert "fleet/timeseries.jsonl" in keys
    assert "fleet/dashboard.html" in keys


def test_pull_restores_files_into_local_tree(mock_r2, isolated_inventory):
    root, inv = isolated_inventory
    # Pre-seed the mock store with remote state.
    mock_r2.store[("test-bucket", "fleet/snapshots/2026-06-10.json")] = b'{"d":"2026-06-10"}'
    mock_r2.store[("test-bucket", "fleet/timeseries.jsonl")] = b'{"row":1}\n'
    mock_r2.store[("test-bucket", "fleet/dashboard.html")] = b"<html>dash</html>"

    out = r2_state.pull_from_r2()
    assert out["files_pulled"] == 3
    # Files land at expected local paths.
    assert (root / "projects/fleet_monitoring/data/snapshots/2026-06-10.json").read_text() \
        == '{"d":"2026-06-10"}'
    assert (root / "projects/fleet_monitoring/data/timeseries.jsonl").read_text() \
        == '{"row":1}\n'
    assert (root / "projects/fleet_monitoring/data/dashboard.html").read_text() \
        == "<html>dash</html>"


def test_pull_handles_missing_files_as_clean_start(mock_r2, isolated_inventory):
    # No files in mock store — first-time run. Should succeed with 0 pulled.
    out = r2_state.pull_from_r2()
    assert out["files_pulled"] == 0
    assert out["errors"] == []


def test_push_then_pull_round_trips_content(mock_r2, isolated_inventory):
    root, inv = isolated_inventory
    src = root / "projects/fleet_monitoring/data/snapshots/2026-06-11.json"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text('{"date":"2026-06-11","run":42}')

    push_out = r2_state.push_to_r2()
    assert push_out["files_pushed"] == 1
    # Nuke local, then pull and verify identity.
    src.unlink()
    pull_out = r2_state.pull_from_r2()
    assert pull_out["files_pulled"] == 1
    assert src.read_text() == '{"date":"2026-06-11","run":42}'


def test_oauth_token_round_trips_when_present(mock_r2, isolated_inventory, monkeypatch):
    root, _ = isolated_inventory
    oauth = r2_state._OAUTH_TOKEN_LOCAL
    oauth.parent.mkdir(parents=True, exist_ok=True)
    oauth.write_text('{"sessions": [{"user": "analyticsuser3"}]}')

    r2_state.push_to_r2()
    assert ("test-bucket", "auth/alltoken.json") in mock_r2.store
    # Wipe and pull.
    oauth.unlink()
    r2_state.pull_from_r2()
    payload = json.loads(oauth.read_text())
    assert payload["sessions"][0]["user"] == "analyticsuser3"


def test_oauth_push_skipped_when_local_absent(mock_r2, isolated_inventory):
    # alltoken.json doesn't exist locally — push must NOT error.
    out = r2_state.push_to_r2()
    assert out["errors"] == []
    assert ("test-bucket", "auth/alltoken.json") not in mock_r2.store


def test_push_does_not_delete_remote_when_local_files_missing(mock_r2,
                                                              isolated_inventory):
    """Local-as-source-of-truth for CONTENT, not for absence. A locally
    missing file must not delete its remote counterpart — otherwise a
    transient file rotation drops history."""
    mock_r2.store[("test-bucket",
                   "fleet/snapshots/2026-05-01.json")] = b'{"old":"snapshot"}'
    r2_state.push_to_r2()
    # Old remote object survives — we didn't write a local equivalent.
    assert ("test-bucket",
            "fleet/snapshots/2026-05-01.json") in mock_r2.store


def test_get_file_reraises_non_404_errors(mock_r2, isolated_inventory):
    """A 401/403/500 from R2 must NOT silently degrade to 'clean start' —
    that would let an auth misconfig masquerade as a fresh run and wipe
    history on the subsequent push."""
    def boom(*a, **kw):
        raise Exception("500 Server Error")
    mock_r2.download_file = boom
    with pytest.raises(Exception, match="500"):
        r2_state._get_file(mock_r2, "test-bucket", "fleet/dashboard.html",
                           Path("/tmp/x"))


def test_get_file_returns_false_on_404(mock_r2):
    """Only missing-key errors should be treated as 'not yet uploaded'."""
    assert r2_state._get_file(mock_r2, "test-bucket", "no/such/key",
                              Path("/tmp/never-created")) is False


def test_inventory_uses_real_pipeline_paths():
    """Smoke test the production inventory points at the actual paths
    the pipeline writes to. Guards against renames silently breaking
    state sync."""
    from projects.fleet_monitoring.models import DATA_DIR
    locals_in_inventory = {str(local) for local, _, _ in r2_state._INVENTORY}
    # Must include the canonical snapshots dir + run-log + dashboard html.
    assert str(DATA_DIR / "snapshots") in locals_in_inventory
    assert str(DATA_DIR / "run-log.jsonl") in locals_in_inventory
    assert str(DATA_DIR / "dashboard.html") in locals_in_inventory
