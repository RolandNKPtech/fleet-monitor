import json
import pytest
from pathlib import Path
from skills.base import SkillStatus
from skills.reporting.infra_report import InfraReportSkill


def _full_data():
    """Pre-collected data dict that skips API calls."""
    return {
        "site_status": {"active": 200, "cancelling": 15, "cancelled": 10, "not_found": 0},
        "wpe": {
            "by_account": {
                "acctA": {
                    "total_installs": 85,
                    "max_db_mb": 650.0,
                    "cpu_usage": {"avg_req_per_visit": 32, "warning_count": 3, "critical_count": 1},
                }
            },
            "totals": {
                "total_installs": 85,
                "avg_req_per_visit": 32,
                "total_php_fatals": 0,
                "warning_sites": 3,
                "critical_sites": 1,
            },
            "alerts": [
                {"install": "badsite", "account": "acctA", "reason": "WARNING: autoloaded_bytes 2,500,000 > 1,048,576 (1 MB)"}
            ],
            "top_db_sites": [
                {"name": "bigdbsite", "account": "acctA", "db_mb": 650.0, "tables": 142, "autoload_kb": 330.0}
            ],
        },
        "cloudflare": {
            "total_zones": 50,
            "o2o_compliant": 30,
            "o2o_non_compliant": 20,
            "compliance_pct": 60.0,
            "by_account": {
                "acctA": {
                    "total_zones": 30,
                    "compliant": 20,
                    "non_compliant": 10,
                    "compliance_pct": 66.7,
                },
                "acctB": {
                    "total_zones": 20,
                    "compliant": 10,
                    "non_compliant": 10,
                    "compliance_pct": 50.0,
                },
            },
            "top_issues": [
                {"domain": "bad.com", "failures": ["ssl_strict"]}
            ],
            "top_failing_checks": [
                {"name": "ssl_strict", "label": "SSL mode not set to Strict", "count": 18},
                {"name": "hsts", "label": "HSTS header not configured", "count": 12},
            ],
        },
        "cf_analytics": {
            "sampled_zones": 25,
            "total_zones": 50,
            "cache_hit_rate": 52.3,
            "total_requests_7d": 500000,
            "total_cached_7d": 261500,
            "total_threats_7d": 45,
            "est_fleet_requests_7d": 1000000,
            "est_fleet_cached_7d": 523000,
            "est_fleet_threats_7d": 90,
        },
        "o2o_baseline": {
            "date": "2026-03-30T18:48:13",
            "zones_audited": 270,
            "compliant": 93,
            "non_compliant": 177,
            "failure_counts": {"ssl_strict": 141, "hsts": 159, "early_hints_on": 160},
        },
        "collection_errors": [],
    }


@pytest.mark.asyncio
async def test_infra_report_generates_html(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "snapshots").mkdir(parents=True)

    skill = InfraReportSkill()
    result = await skill.run(target="all", data=_full_data())

    assert result.status == SkillStatus.SUCCESS
    assert "html_path" in result.data["paths"]

    html_path = Path(result.data["paths"]["html_path"])
    assert html_path.exists()
    html = html_path.read_text()
    assert "Server & Cloudflare Performance Report" in html
    assert "Executive Summary" in html
    assert "acctA" in html
    assert "SSL mode not set to Strict" in html
    assert "Action Items" in html
    assert "Cache Performance" in html
    assert "O2O Project Status" in html
    assert "52.3%" in html


@pytest.mark.asyncio
async def test_infra_report_saves_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "snapshots").mkdir(parents=True)

    skill = InfraReportSkill()
    result = await skill.run(target="all", data=_full_data())

    snap_path = Path(result.data["snapshot_path"])
    assert snap_path.exists()
    snap = json.loads(snap_path.read_text())
    assert snap["wpe"]["totals"]["total_installs"] == 85


@pytest.mark.asyncio
async def test_infra_report_computes_deltas(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    snap_dir = tmp_path / "data" / "snapshots"
    snap_dir.mkdir(parents=True)

    prev = {
        "date": "2026-03-01",
        "wpe": {
            "totals": {"total_installs": 80, "warning_sites": 5, "critical_sites": 3},
            "by_account": {},
        },
        "cloudflare": {
            "total_zones": 48, "o2o_compliant": 25, "o2o_non_compliant": 21,
            "compliance_pct": 52.1,
            "by_account": {},
            "top_issues": [],
        },
    }
    (snap_dir / "2026-03-01.json").write_text(json.dumps(prev))

    skill = InfraReportSkill()
    result = await skill.run(target="all", data=_full_data())

    html = Path(result.data["paths"]["html_path"]).read_text()
    assert "Baseline snapshot" not in html


@pytest.mark.asyncio
async def test_infra_report_empty_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "snapshots").mkdir(parents=True)

    skill = InfraReportSkill()
    result = await skill.run(target="all", data={
        "wpe": {"by_account": {}, "totals": {"total_installs": 0, "warning_sites": 0, "critical_sites": 0}, "alerts": [], "top_db_sites": []},
        "cloudflare": {"total_zones": 0, "o2o_compliant": 0, "o2o_non_compliant": 0, "compliance_pct": 0, "by_account": {}, "top_issues": [], "top_failing_checks": []},
        "cf_analytics": {},
        "o2o_baseline": {},
        "collection_errors": [],
    })
    assert result.status == SkillStatus.SUCCESS


@pytest.mark.asyncio
async def test_infra_report_action_items(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "snapshots").mkdir(parents=True)

    data = _full_data()
    # Add an account below 50%
    data["cloudflare"]["by_account"]["acctC"] = {
        "total_zones": 10, "compliant": 3, "non_compliant": 7, "compliance_pct": 30.0,
    }

    skill = InfraReportSkill()
    result = await skill.run(target="all", data=data)

    html = Path(result.data["paths"]["html_path"]).read_text()
    assert "Remediate acctC" in html
    assert "badsite" in html  # WPE alert
