import json
import pytest
import httpx
import respx
from skills.wpengine.install_info import InstallInfoSkill
from skills.wpengine.client import _reset_client
from skills.base import SkillStatus

BASE = "https://api.wpengineapi.com/v1"


@pytest.fixture(autouse=True)
def reset_wpe_client():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return InstallInfoSkill()


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Create a temp config with test sites for acctA."""
    sites = {
        "sites": [
            {
                "domain": "drjones.com",
                "wpe_account": "acctA",
                "wpe_install": "drjones",
                "active": True,
            },
            {
                "domain": "smithderm.com",
                "wpe_account": "acctA",
                "wpe_install": "smithderm",
                "active": True,
            },
        ]
    }
    accounts = {"wpengine": {"acctA": {"id": "acc_001", "label": "NKP Medical 1"}}}
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sites.json").write_text(json.dumps(sites))
    (data_dir / "accounts.json").write_text(json.dumps(accounts))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _installs_response():
    from tests.conftest import load_fixture
    return load_fixture("wpe_installs")


# --- Test 1: Get installs for an account → SUCCESS with install list ---

@pytest.mark.asyncio
@respx.mock
async def test_installs_for_account(skill, config_dir, monkeypatch):
    monkeypatch.setenv("WPE_API_USER", "u")
    monkeypatch.setenv("WPE_API_PASSWORD", "p")

    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=_installs_response())
    )

    result = await skill.run(target="acctA")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["count"] == 2
    installs = result.data["installs"]
    assert any(i["name"] == "drjones" for i in installs)
    assert any(i["name"] == "smithderm" for i in installs)
    # Verify fields are formatted correctly
    first = next(i for i in installs if i["name"] == "drjones")
    assert first["environment"] == "production"
    assert first["php_version"] == "8.2"
    assert first["primary_domain"] == "drjones.com"
    assert first["status"] == "active"


# --- Test 2: Get all installs → SUCCESS ---

@pytest.mark.asyncio
@respx.mock
async def test_all_installs(skill, config_dir, monkeypatch):
    monkeypatch.setenv("WPE_API_USER", "u")
    monkeypatch.setenv("WPE_API_PASSWORD", "p")

    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=_installs_response())
    )

    result = await skill.run(target="all")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["count"] == 2
    names = [i["name"] for i in result.data["installs"]]
    assert "drjones" in names
    assert "smithderm" in names


# --- Test 3: Single install by name → SUCCESS with 1 result ---

@pytest.mark.asyncio
@respx.mock
async def test_single_install_by_name(skill, config_dir, monkeypatch):
    monkeypatch.setenv("WPE_API_USER", "u")
    monkeypatch.setenv("WPE_API_PASSWORD", "p")

    # get_install_by_name calls get_installs which fetches /installs
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=_installs_response())
    )

    result = await skill.run(target="drjones")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["count"] == 1
    assert result.data["installs"][0]["name"] == "drjones"
    assert result.data["installs"][0]["primary_domain"] == "drjones.com"


# --- Test 4: Not found → SUCCESS with empty list ---

@pytest.mark.asyncio
@respx.mock
async def test_install_not_found(skill, config_dir, monkeypatch):
    monkeypatch.setenv("WPE_API_USER", "u")
    monkeypatch.setenv("WPE_API_PASSWORD", "p")

    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={"results": [], "count": 0})
    )

    result = await skill.run(target="nonexistent")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["count"] == 0
    assert result.data["installs"] == []
