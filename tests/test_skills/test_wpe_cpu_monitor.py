"""
Tests for wpengine.cpu_monitor skill.

Coverage:
1. Normal metrics → SUCCESS, environments listed with ratios
2. High req/visit ratio (> 50) → WARNING with flagged sites
3. High autoloaded bytes (> 1 MB) → WARNING
4. Large database (> 2 GB) → WARNING
5. Null string values in response → handled gracefully, values default to 0
"""
import pytest
import respx
import httpx
from skills.wpengine.cpu_monitor import CPUMonitorSkill
from skills.wpengine.client import _reset_client
from skills.base import SkillStatus
from tests.conftest import load_fixture

BASE = "https://api.wpengineapi.com/v1"
ACCOUNT_ID = "test-account-id"


@pytest.fixture(autouse=True)
def reset_client():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return CPUMonitorSkill()


@pytest.fixture(autouse=True)
def wpe_env(monkeypatch):
    monkeypatch.setenv("WPE_API_USER", "test-user")
    monkeypatch.setenv("WPE_API_PASSWORD", "test-pass")


# ---------------------------------------------------------------------------
# Test 1: Normal metrics → SUCCESS, no flags
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_normal_metrics_success(skill):
    """Normal fixture (ratio ≈ 26.7) → SUCCESS with environment data."""
    fixture = load_fixture("wpe_usage_metrics")
    respx.get(f"{BASE}/accounts/{ACCOUNT_ID}/usage").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    result = await skill.run(target=ACCOUNT_ID)

    assert result.status == SkillStatus.SUCCESS
    assert result.data is not None
    envs = result.data["environments"]
    assert len(envs) == 1
    env = envs[0]
    assert env["name"] == "drjones"
    # ratio = 12000 / 450 ≈ 26.7 — below warning threshold
    assert env["req_visit_ratio"] == pytest.approx(26.7, abs=0.1)
    assert env["flags"] == []
    assert result.data["flagged"] == []


# ---------------------------------------------------------------------------
# Test 2: High req/visit ratio → WARNING with flagged sites
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_high_req_visit_ratio_warning(skill):
    """When req/visit ratio > 50 the environment should be flagged as WARNING."""
    # 6000 visits, 360000 requests → ratio = 60 (> 50, WARNING)
    high_ratio_data = {
        "environment_metrics": [
            {
                "environment_name": "highload-site",
                "metrics": [
                    {
                        "date": "2026-03-29",
                        "visit_count": 6000,
                        "request_origin_count": 360000,
                        "network_origin_bytes": 500000000,
                        "network_cdn_bytes": 1000000000,
                        "storage_database_bytes": 524288000,
                        "storage_file_bytes": 2147483648,
                        "database_tables_count": 300,
                        "autoloaded_bytes": 512000,
                    }
                ],
            }
        ],
        "total_size": 1,
    }
    respx.get(f"{BASE}/accounts/{ACCOUNT_ID}/usage").mock(
        return_value=httpx.Response(200, json=high_ratio_data)
    )

    result = await skill.run(target=ACCOUNT_ID)

    assert result.status == SkillStatus.WARNING
    env = result.data["environments"][0]
    assert env["req_visit_ratio"] == 60.0
    assert any("req/visit ratio" in f for f in env["flags"])
    assert "highload-site" in result.data["flagged"]
    assert "highload-site" in result.message


@pytest.mark.asyncio
@respx.mock
async def test_critical_req_visit_ratio(skill):
    """When req/visit ratio > 100 the environment is flagged CRITICAL (still WARNING status)."""
    # 1000 visits, 150000 requests → ratio = 150 (> 100, CRITICAL flag)
    critical_data = {
        "environment_metrics": [
            {
                "environment_name": "critical-site",
                "metrics": [
                    {
                        "date": "2026-03-29",
                        "visit_count": 1000,
                        "request_origin_count": 150000,
                        "network_origin_bytes": 100000000,
                        "network_cdn_bytes": 200000000,
                        "storage_database_bytes": 524288000,
                        "storage_file_bytes": 1073741824,
                        "database_tables_count": 120,
                        "autoloaded_bytes": 400000,
                    }
                ],
            }
        ],
        "total_size": 1,
    }
    respx.get(f"{BASE}/accounts/{ACCOUNT_ID}/usage").mock(
        return_value=httpx.Response(200, json=critical_data)
    )

    result = await skill.run(target=ACCOUNT_ID)

    assert result.status == SkillStatus.WARNING
    env = result.data["environments"][0]
    assert any("CRITICAL" in f for f in env["flags"])
    assert "critical-site" in result.data["flagged"]


# ---------------------------------------------------------------------------
# Test 3: High autoloaded bytes → WARNING
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_high_autoloaded_bytes_warning(skill):
    """autoloaded_bytes > 1 MB (1_048_576) → WARNING flag."""
    autoload_data = {
        "environment_metrics": [
            {
                "environment_name": "heavy-autoload",
                "metrics": [
                    {
                        "date": "2026-03-29",
                        "visit_count": 500,
                        "request_origin_count": 10000,   # ratio = 20, under threshold
                        "network_origin_bytes": 50000000,
                        "network_cdn_bytes": 100000000,
                        "storage_database_bytes": 524288000,
                        "storage_file_bytes": 1073741824,
                        "database_tables_count": 200,
                        "autoloaded_bytes": 2_097_152,   # 2 MB — over 1 MB threshold
                    }
                ],
            }
        ],
        "total_size": 1,
    }
    respx.get(f"{BASE}/accounts/{ACCOUNT_ID}/usage").mock(
        return_value=httpx.Response(200, json=autoload_data)
    )

    result = await skill.run(target=ACCOUNT_ID)

    assert result.status == SkillStatus.WARNING
    env = result.data["environments"][0]
    assert any("autoloaded_bytes" in f for f in env["flags"])
    assert "heavy-autoload" in result.data["flagged"]


# ---------------------------------------------------------------------------
# Test 4: Large database → WARNING
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_large_database_warning(skill):
    """storage_database_bytes > 2 GB (2_147_483_648) → WARNING flag."""
    db_data = {
        "environment_metrics": [
            {
                "environment_name": "bigdb-site",
                "metrics": [
                    {
                        "date": "2026-03-29",
                        "visit_count": 300,
                        "request_origin_count": 6000,    # ratio = 20, under threshold
                        "network_origin_bytes": 40000000,
                        "network_cdn_bytes": 80000000,
                        "storage_database_bytes": 4_294_967_296,  # 4 GB — over 2 GB threshold
                        "storage_file_bytes": 5_368_709_120,
                        "database_tables_count": 600,
                        "autoloaded_bytes": 300000,
                    }
                ],
            }
        ],
        "total_size": 1,
    }
    respx.get(f"{BASE}/accounts/{ACCOUNT_ID}/usage").mock(
        return_value=httpx.Response(200, json=db_data)
    )

    result = await skill.run(target=ACCOUNT_ID)

    assert result.status == SkillStatus.WARNING
    env = result.data["environments"][0]
    assert any("storage_database_bytes" in f for f in env["flags"])
    assert "bigdb-site" in result.data["flagged"]


# ---------------------------------------------------------------------------
# Test 5: Null string values → handled gracefully, values default to 0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_null_string_values_handled(skill):
    """WPE sometimes returns "null" as a string — must not crash, values → 0."""
    fixture = load_fixture("wpe_usage_null_values")
    respx.get(f"{BASE}/accounts/{ACCOUNT_ID}/usage").mock(
        return_value=httpx.Response(200, json=fixture)
    )

    result = await skill.run(target=ACCOUNT_ID)

    # Should not raise; status is SUCCESS (no thresholds exceeded)
    assert result.status == SkillStatus.SUCCESS
    env = result.data["environments"][0]
    assert env["name"] == "newsite"
    # All "null" string fields parsed as 0
    assert env["visit_count"] == 0
    assert env["request_origin_count"] == 0
    assert env["autoloaded_bytes"] == 0
    # ratio is None when visits == 0
    assert env["req_visit_ratio"] is None
    # No flags for this environment
    assert env["flags"] == []
