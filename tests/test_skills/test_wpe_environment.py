"""
Tests for wpengine.environment skill.

Covers: prod+staging match, PHP version drift, single environment, install not found.
"""
import pytest
import respx
import httpx

from skills.wpengine.environment import EnvironmentSkill
from skills.wpengine.client import _reset_client
from skills.base import SkillStatus

BASE = "https://api.wpengineapi.com/v1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_wpe_client():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill(monkeypatch):
    monkeypatch.setenv("WPE_API_USER", "test-user")
    monkeypatch.setenv("WPE_API_PASSWORD", "test-pass")
    return EnvironmentSkill()


def _installs_response(*installs: dict) -> dict:
    return {"results": list(installs), "count": len(installs)}


def _make_install(
    name: str,
    environment: str,
    php_version: str = "8.2",
    status: str = "active",
    account_id: str = "acc_001",
    wp_version: str | None = None,
    primary_domain: str | None = None,
) -> dict:
    inst: dict = {
        "id": f"inst_{name}",
        "name": name,
        "environment": environment,
        "php_version": php_version,
        "status": status,
        "cname": f"{name}.wpengine.com",
        "is_multisite": False,
        "account": {"id": account_id},
        "primary_domain": primary_domain or f"{name}.com",
    }
    if wp_version is not None:
        inst["wp_version"] = wp_version
    return inst


# ---------------------------------------------------------------------------
# Test 1: Production + staging exist, same PHP version → SUCCESS, no diffs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_prod_and_staging_match(skill):
    """Prod + staging with identical PHP → SUCCESS with no diffs reported."""
    prod = _make_install("drjones", "production", php_version="8.2")
    stg = _make_install("drjonesstg", "staging", php_version="8.2")

    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=_installs_response(prod, stg))
    )

    result = await skill.run(target="drjones")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["diffs"] == []
    assert result.data["site"] == "drjones"
    assert result.data["production"]["name"] == "drjones"
    assert len(result.data["others"]) == 1
    assert result.data["others"][0]["name"] == "drjonesstg"
    assert result.data["others"][0]["diffs"] == []


# ---------------------------------------------------------------------------
# Test 2: PHP version differs between prod and staging → WARNING with diff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_php_version_differs(skill):
    """Prod PHP 8.2 vs staging PHP 8.1 → WARNING with diff details."""
    prod = _make_install("drjones", "production", php_version="8.2")
    stg = _make_install("drjonesstg", "staging", php_version="8.1")

    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=_installs_response(prod, stg))
    )

    result = await skill.run(target="drjones")

    assert result.status == SkillStatus.WARNING
    assert len(result.data["diffs"]) == 1
    diff = result.data["diffs"][0]
    assert "8.2" in diff
    assert "8.1" in diff
    assert "PHP" in diff
    # Message should surface the diff
    assert "8.2" in result.message or "PHP" in result.message


# ---------------------------------------------------------------------------
# Test 3: No staging found → SUCCESS, message says "single environment only"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_no_staging_found(skill):
    """Only production install found → SUCCESS with single-environment message."""
    prod = _make_install("drjones", "production", php_version="8.2")

    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=_installs_response(prod))
    )

    result = await skill.run(target="drjones")

    assert result.status == SkillStatus.SUCCESS
    assert "single environment" in result.message.lower()
    assert result.data["others"] == []
    assert result.data["diffs"] == []


# ---------------------------------------------------------------------------
# Test 4: Install not found → FAILURE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_install_not_found(skill):
    """Target install name not in WPE API → FAILURE."""
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={"results": [], "count": 0})
    )

    result = await skill.run(target="nonexistent")

    assert result.status == SkillStatus.FAILURE
    assert "nonexistent" in result.message or "not found" in result.message.lower()
    assert len(result.errors) > 0
