import pytest
import respx
import httpx
from skills.wpengine.cache_manage import CacheManageSkill
from skills.wpengine.client import _reset_client
from skills.base import SkillStatus

BASE = "https://api.wpengineapi.com/v1"

INSTALL_NAME = "mysite"
INSTALL_ID = "inst_abc123"

INSTALLS_RESPONSE = {
    "results": [
        {
            "id": INSTALL_ID,
            "name": INSTALL_NAME,
            "environment": "production",
            "php_version": "8.2",
            "primary_domain": "mysite.com",
            "status": "active",
            "account": {"id": "acc_001"},
        }
    ],
    "count": 1,
}


@pytest.fixture(autouse=True)
def reset_wpe_client():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return CacheManageSkill()


# --- Test 1: Purge success → SUCCESS ---

@pytest.mark.asyncio
@respx.mock
async def test_purge_cache_success(skill, monkeypatch):
    """Successful cache purge returns SUCCESS with install info."""
    monkeypatch.setenv("WPE_API_USER", "u")
    monkeypatch.setenv("WPE_API_PASSWORD", "p")

    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=INSTALLS_RESPONSE)
    )
    respx.post(f"{BASE}/installs/{INSTALL_ID}/purge_cache").mock(
        return_value=httpx.Response(200, json={"id": INSTALL_ID})
    )

    result = await skill.run(target=INSTALL_NAME)

    assert result.status == SkillStatus.SUCCESS
    assert result.data["install"] == INSTALL_NAME
    assert result.data["install_id"] == INSTALL_ID
    assert INSTALL_NAME in result.message


# --- Test 2: Install not found → FAILURE ---

@pytest.mark.asyncio
@respx.mock
async def test_install_not_found(skill, monkeypatch):
    """Install lookup returns None → FAILURE with descriptive error."""
    monkeypatch.setenv("WPE_API_USER", "u")
    monkeypatch.setenv("WPE_API_PASSWORD", "p")

    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={"results": [], "count": 0})
    )

    result = await skill.run(target="nonexistent")

    assert result.status == SkillStatus.FAILURE
    assert result.errors
    assert "nonexistent" in result.message


# --- Test 3: API error on purge → FAILURE ---

@pytest.mark.asyncio
@respx.mock
async def test_api_error_on_purge(skill, monkeypatch):
    """Purge endpoint returns a server error → FAILURE."""
    monkeypatch.setenv("WPE_API_USER", "u")
    monkeypatch.setenv("WPE_API_PASSWORD", "p")

    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=INSTALLS_RESPONSE)
    )
    respx.post(f"{BASE}/installs/{INSTALL_ID}/purge_cache").mock(
        return_value=httpx.Response(500, json={"message": "Internal Server Error"})
    )

    result = await skill.run(target=INSTALL_NAME)

    assert result.status == SkillStatus.FAILURE
    assert result.errors
