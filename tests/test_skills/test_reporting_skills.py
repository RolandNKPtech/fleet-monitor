import pytest
from skills.reporting.monthly_report import MonthlyReportSkill
from skills.reporting.audit_report import AuditReportSkill
from skills.base import SkillStatus
from pathlib import Path

# ---------------------------------------------------------------------------
# performance_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monthly_report_with_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)

    skill = MonthlyReportSkill()
    result = await skill.run(
        target="acctA",
        month="March 2026",
        data={
            "summary": "Great month. 42 sites healthy.",
            "total_installs": 42,
            "total_zones": 38,
            "account_count": 1,
            "compliance_pct": 95,
            "critical_issues": 0,
            "drift_items": [{"domain": "test.com", "issue": "SSL flexible", "severity": "critical"}],
            "action_items": [{"priority": 1, "text": "Fix SSL on test.com"}],
        }
    )
    assert result.status == SkillStatus.SUCCESS
    assert "html_path" in result.data["paths"]
    html = Path(result.data["paths"]["html_path"]).read_text()
    assert "NKP MEDICAL MARKETING" in html
    assert "42" in html
    assert "test.com" in html


@pytest.mark.asyncio
async def test_monthly_report_empty_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)

    skill = MonthlyReportSkill()
    result = await skill.run(target="acctA")
    assert result.status == SkillStatus.SUCCESS


@pytest.mark.asyncio
async def test_audit_report_with_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)

    skill = AuditReportSkill()
    result = await skill.run(
        target="drjones.com",
        data={
            "summary": "Audit complete. 1 issue found.",
            "sites_audited": 1,
            "checks_passed": 9,
            "issues_found": 1,
            "compliance_pct": 90,
            "checks": [
                {"domain": "drjones.com", "name": "SSL", "passed": True, "details": "strict"},
                {"domain": "drjones.com", "name": "APO", "passed": False, "details": "enabled (should be off)"},
            ],
        }
    )
    assert result.status == SkillStatus.SUCCESS
    html = Path(result.data["paths"]["html_path"]).read_text()
    assert "drjones.com" in html
    assert "Pass" in html
    assert "Fail" in html


@pytest.mark.asyncio
async def test_audit_report_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)

    skill = AuditReportSkill()
    result = await skill.run(target="test.com")
    assert result.status == SkillStatus.SUCCESS


# ---------------------------------------------------------------------------
# performance_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_performance_report_with_metrics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)

    from skills.reporting.performance_report import PerformanceReportSkill
    skill = PerformanceReportSkill()
    result = await skill.run(
        target="drjones.com",
        data={
            "summary": "O2O deployment improved cache hit rate by 45%.",
            "metrics": [
                {"name": "Cache Hit Rate", "before": "35%", "after": "80%", "change": "+45%", "improved": True, "declined": False},
                {"name": "Origin Requests", "before": "12,000/day", "after": "4,800/day", "change": "-60%", "improved": True, "declined": False},
            ],
            "findings": [{"severity": "low", "text": "Cache performing as expected after O2O"}],
        }
    )
    assert result.status == SkillStatus.SUCCESS
    html = Path(result.data["paths"]["html_path"]).read_text()
    assert "Cache Hit Rate" in html
    assert "Improved" in html


@pytest.mark.asyncio
async def test_performance_report_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)

    from skills.reporting.performance_report import PerformanceReportSkill
    skill = PerformanceReportSkill()
    result = await skill.run(target="test.com")
    assert result.status == SkillStatus.SUCCESS


# ---------------------------------------------------------------------------
# executive_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executive_summary_with_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)

    from skills.reporting.executive_summary import ExecutiveSummarySkill
    skill = ExecutiveSummarySkill()
    result = await skill.run(
        target="acctA",
        data={
            "summary": "42 sites managed. 99.7% uptime. 0 security incidents.",
            "kpis": [
                {"value": "42", "label": "Sites", "delta": "", "delta_class": "neutral"},
                {"value": "99.7%", "label": "Uptime", "delta": "+0.2%", "delta_class": "positive"},
            ],
            "highlights": [{"severity": "low", "title": "Clean month", "detail": "No critical incidents"}],
            "action_items": [{"priority": 3, "text": "Review plugin updates for Q2"}],
        }
    )
    assert result.status == SkillStatus.SUCCESS
    html = Path(result.data["paths"]["html_path"]).read_text()
    assert "42" in html
    assert "99.7%" in html


@pytest.mark.asyncio
async def test_executive_summary_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)

    from skills.reporting.executive_summary import ExecutiveSummarySkill
    skill = ExecutiveSummarySkill()
    result = await skill.run(target="test.com")
    assert result.status == SkillStatus.SUCCESS
