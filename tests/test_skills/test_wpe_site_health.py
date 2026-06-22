"""
Tests for wpengine.site_health skill.

Uses respx to mock WPE API calls and unittest.mock.patch for paramiko SSH.
"""
import pytest
import respx
import httpx
from unittest.mock import MagicMock, patch

from skills.wpengine.site_health import SiteHealthSkill
from skills.wpengine.client import _reset_client
from skills.base import SkillStatus

BASE = "https://api.wpengineapi.com/v1"

# --- Fixtures ---

@pytest.fixture(autouse=True)
def reset_wpe_client():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill(monkeypatch):
    monkeypatch.setenv("WPE_API_USER", "test-user")
    monkeypatch.setenv("WPE_API_PASSWORD", "test-pass")
    return SiteHealthSkill()


# --- Mock helpers ---

def _mock_installs(install_name="drjones", install_id="inst_001", status="active",
                   php_version="8.2", primary_domain="drjones.com", account_id="acc_001"):
    """Mock the /installs list endpoint."""
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={
            "results": [{
                "id": install_id,
                "name": install_name,
                "environment": "production",
                "status": status,
                "cname": f"{install_name}.wpengine.com",
                "php_version": php_version,
                "is_multisite": False,
                "account": {"id": account_id},
                "primary_domain": primary_domain,
            }],
            "count": 1,
        })
    )


def _mock_usage(account_id="acc_001", install_name="drjones", origin_requests=500):
    """Mock the /accounts/{id}/usage endpoint."""
    respx.get(f"{BASE}/accounts/{account_id}/usage").mock(
        return_value=httpx.Response(200, json={
            "environment_metrics": [{
                "environment_name": install_name,
                "metrics": [{
                    "date": "2026-03-29",
                    "visit_count": 120,
                    "request_origin_count": origin_requests,
                    "autoloaded_bytes": 512000,
                    "storage_database_bytes": 524288000,
                }],
            }],
            "total_size": 1,
        })
    )


def _mock_ssh_success(log_content="No errors here\n"):
    """Patch WPESSHClient to return a successful SSH tail_log result."""
    mock_ssh = MagicMock()
    mock_ssh.tail_log = MagicMock(return_value=_async_return(log_content))
    return mock_ssh


def _mock_ssh_failure():
    """Patch WPESSHClient to raise APIError (SSH unavailable)."""
    from core.errors import APIError
    mock_ssh = MagicMock()
    mock_ssh.tail_log = MagicMock(side_effect=APIError("ssh", None, "Connection refused"))
    return mock_ssh


async def _async_return(value):
    return value


# --- Tests ---

@pytest.mark.asyncio
@respx.mock
async def test_healthy_site(skill, monkeypatch):
    """Test 1: Healthy site → SUCCESS, health_status='good'"""
    _mock_installs()
    _mock_usage(origin_requests=500)

    clean_log = "WordPress started OK\n"
    mock_ssh = _mock_ssh_success(clean_log)

    with patch("skills.wpengine.site_health.WPESSHClient", return_value=mock_ssh):
        with patch("skills.wpengine.site_health.NKPConfig") as MockConfig:
            cfg_instance = MagicMock()
            cfg_instance.get_active_sites.return_value = [{
                "wpe_install": "drjones",
                "wpe_account": "acctA",
            }]
            cfg_instance.get_account.return_value = {"id": "acc_001", "label": "NKP Medical 1"}
            MockConfig.return_value = cfg_instance

            result = await skill.run(target="drjones")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["health_status"] == "good"
    assert result.data["install"]["status"] == "active"
    assert result.data["install"]["php_version"] == "8.2"
    assert result.data["fatal_count"] == 0
    assert result.data["error_count"] == 0
    assert result.data["ssh_status"] == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_high_origin_requests(skill):
    """Test 2: High origin requests → WARNING, health_status='warning'"""
    _mock_installs()
    _mock_usage(origin_requests=15000)  # > 10,000 threshold

    clean_log = ""
    mock_ssh = _mock_ssh_success(clean_log)

    with patch("skills.wpengine.site_health.WPESSHClient", return_value=mock_ssh):
        with patch("skills.wpengine.site_health.NKPConfig") as MockConfig:
            cfg_instance = MagicMock()
            cfg_instance.get_active_sites.return_value = [{
                "wpe_install": "drjones",
                "wpe_account": "acctA",
            }]
            cfg_instance.get_account.return_value = {"id": "acc_001", "label": "NKP Medical 1"}
            MockConfig.return_value = cfg_instance

            result = await skill.run(target="drjones")

    assert result.status == SkillStatus.WARNING
    assert result.data["health_status"] == "warning"
    assert result.data["usage"]["origin_requests"] == 15000
    assert any("15,000" in w or "origin" in w.lower() for w in result.data["warnings"])


@pytest.mark.asyncio
@respx.mock
async def test_php_fatals_in_log(skill):
    """Test 3: PHP fatals in SSH log → WARNING with error count"""
    _mock_installs()
    _mock_usage(origin_requests=500)

    fatal_log = (
        "[29-Mar-2026 14:23:01 UTC] PHP Fatal error: Uncaught Error in /wp-content/plugins/bad-plugin/file.php:42\n"
        "[29-Mar-2026 14:24:02 UTC] PHP Warning: Invalid argument in /wp-content/plugins/divi/file.php:123\n"
        "[29-Mar-2026 14:25:30 UTC] PHP Fatal error: Allowed memory size exhausted in /wp-content/plugins/perfmatters/file.php:200\n"
    )
    mock_ssh = _mock_ssh_success(fatal_log)

    with patch("skills.wpengine.site_health.WPESSHClient", return_value=mock_ssh):
        with patch("skills.wpengine.site_health.NKPConfig") as MockConfig:
            cfg_instance = MagicMock()
            cfg_instance.get_active_sites.return_value = [{
                "wpe_install": "drjones",
                "wpe_account": "acctA",
            }]
            cfg_instance.get_account.return_value = {"id": "acc_001", "label": "NKP Medical 1"}
            MockConfig.return_value = cfg_instance

            result = await skill.run(target="drjones")

    assert result.status == SkillStatus.WARNING
    assert result.data["health_status"] == "critical"
    assert result.data["fatal_count"] == 2
    assert result.data["error_count"] == 3  # 2 fatals + 1 warning
    assert result.data["ssh_status"] == "ok"
    assert any("fatal" in w.lower() for w in result.data["warnings"])


@pytest.mark.asyncio
@respx.mock
async def test_ssh_unavailable(skill):
    """Test 4: SSH unavailable → still SUCCESS with API data, ssh_status='unavailable'"""
    _mock_installs()
    _mock_usage(origin_requests=200)

    mock_ssh = _mock_ssh_failure()

    with patch("skills.wpengine.site_health.WPESSHClient", return_value=mock_ssh):
        with patch("skills.wpengine.site_health.NKPConfig") as MockConfig:
            cfg_instance = MagicMock()
            cfg_instance.get_active_sites.return_value = [{
                "wpe_install": "drjones",
                "wpe_account": "acctA",
            }]
            cfg_instance.get_account.return_value = {"id": "acc_001", "label": "NKP Medical 1"}
            MockConfig.return_value = cfg_instance

            result = await skill.run(target="drjones")

    # Should still succeed — API data available even without SSH
    assert result.status == SkillStatus.SUCCESS
    assert result.data["ssh_status"] == "unavailable"
    assert result.data["install"]["name"] == "drjones"
    assert result.data["install"]["status"] == "active"
    assert result.data["fatal_count"] == 0
    assert result.data["error_count"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_install_not_found(skill):
    """Test 5: Install not found → FAILURE"""
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={"results": [], "count": 0})
    )

    result = await skill.run(target="nonexistent-install")

    assert result.status == SkillStatus.FAILURE
    assert "not found" in result.message.lower() or "nonexistent-install" in result.message
