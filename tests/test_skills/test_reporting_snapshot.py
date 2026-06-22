# tests/test_skills/test_reporting_snapshot.py
import json
import pytest
from pathlib import Path
from skills.reporting.snapshot import SnapshotManager


@pytest.fixture
def snap_dir(tmp_path):
    d = tmp_path / "snapshots"
    d.mkdir()
    return d


@pytest.fixture
def sample_snapshot():
    return {
        "date": "2026-04-02",
        "wpe": {
            "totals": {
                "total_installs": 509,
                "avg_req_per_visit": 28,
                "total_php_fatals": 34,
                "warning_sites": 8,
                "critical_sites": 2,
            },
            "by_account": {},
        },
        "cloudflare": {
            "total_zones": 273,
            "o2o_compliant": 93,
            "o2o_non_compliant": 177,
            "not_on_cf": 3,
            "compliance_pct": 34.4,
            "performance": {
                "avg_cache_hit_rate": 82.5,
                "total_requests_24h": 1250000,
                "total_threats_blocked": 4500,
                "threat_pct": 0.36,
            },
            "by_account": {},
            "top_issues": [],
        },
    }


class TestSnapshotSave:
    def test_save_creates_json_file(self, snap_dir, sample_snapshot):
        mgr = SnapshotManager(snap_dir)
        path = mgr.save(sample_snapshot)
        assert path.exists()
        assert path.name == "2026-04-02.json"
        data = json.loads(path.read_text())
        assert data["date"] == "2026-04-02"

    def test_save_overwrites_same_day(self, snap_dir, sample_snapshot):
        mgr = SnapshotManager(snap_dir)
        mgr.save(sample_snapshot)
        sample_snapshot["wpe"]["totals"]["total_installs"] = 510
        path = mgr.save(sample_snapshot)
        data = json.loads(path.read_text())
        assert data["wpe"]["totals"]["total_installs"] == 510

    def test_save_creates_directory_if_missing(self, tmp_path, sample_snapshot):
        snap_dir = tmp_path / "new" / "snapshots"
        mgr = SnapshotManager(snap_dir)
        path = mgr.save(sample_snapshot)
        assert path.exists()


class TestSnapshotLoadPrevious:
    def test_no_previous_returns_none(self, snap_dir):
        mgr = SnapshotManager(snap_dir)
        assert mgr.load_previous("2026-04-02") is None

    def test_finds_most_recent_before_date(self, snap_dir):
        for date, val in [("2026-03-01", 500), ("2026-03-15", 505)]:
            data = {"date": date, "wpe": {"totals": {"total_installs": val}}, "cloudflare": {}}
            (snap_dir / f"{date}.json").write_text(json.dumps(data))
        mgr = SnapshotManager(snap_dir)
        prev = mgr.load_previous("2026-04-02")
        assert prev is not None
        assert prev["date"] == "2026-03-15"

    def test_excludes_same_day(self, snap_dir):
        data = {"date": "2026-04-02", "wpe": {}, "cloudflare": {}}
        (snap_dir / "2026-04-02.json").write_text(json.dumps(data))
        mgr = SnapshotManager(snap_dir)
        assert mgr.load_previous("2026-04-02") is None


class TestComputeDeltas:
    def test_computes_numeric_deltas(self, snap_dir):
        mgr = SnapshotManager(snap_dir)
        current = {
            "wpe": {"totals": {"total_installs": 512, "total_php_fatals": 30, "critical_sites": 1}},
            "cloudflare": {"o2o_compliant": 100, "compliance_pct": 36.6, "performance": {"avg_cache_hit_rate": 85.0}},
        }
        previous = {
            "wpe": {"totals": {"total_installs": 509, "total_php_fatals": 34, "critical_sites": 2}},
            "cloudflare": {"o2o_compliant": 93, "compliance_pct": 34.4, "performance": {"avg_cache_hit_rate": 82.5}},
        }
        deltas = mgr.compute_deltas(current, previous)
        assert deltas["wpe"]["totals"]["total_installs"]["value"] == 3
        assert deltas["wpe"]["totals"]["total_installs"]["direction"] == "up"
        assert deltas["wpe"]["totals"]["total_php_fatals"]["value"] == -4
        assert deltas["wpe"]["totals"]["total_php_fatals"]["direction"] == "down"
        assert deltas["cloudflare"]["o2o_compliant"]["value"] == 7
        assert deltas["cloudflare"]["performance"]["avg_cache_hit_rate"]["value"] == pytest.approx(2.5)

    def test_returns_empty_when_no_previous(self, snap_dir):
        mgr = SnapshotManager(snap_dir)
        deltas = mgr.compute_deltas({"wpe": {}, "cloudflare": {}}, None)
        assert deltas == {}
