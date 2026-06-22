import pytest
import json
import respx
import httpx
from skills.cloudflare.wpe_crossref import WpeCrossrefSkill
from skills.cloudflare.client import _reset_client
from skills.base import SkillStatus


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return WpeCrossrefSkill()


def _mock_zones(zones, total_pages=1):
    """Helper to mock CF zone list response."""
    respx.get("https://api.cloudflare.com/client/v4/zones").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": zones,
            "result_info": {
                "page": 1,
                "per_page": 50,
                "count": len(zones),
                "total_count": len(zones),
                "total_pages": total_pages,
            },
        })
    )


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Create a temp config with test WPE sites."""
    sites = {"sites": [
        {"domain": "drjones.com", "wpe_account": "acctA", "active": True},
        {"domain": "smithderm.com", "wpe_account": "acctA", "active": True},
    ]}
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sites.json").write_text(json.dumps(sites))
    (data_dir / "accounts.json").write_text('{"wpengine": {}}')
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- Test 1: All matched ---

@pytest.mark.asyncio
@respx.mock
async def test_all_matched(skill, config_dir, monkeypatch):
    """All WPE sites exist in CF and vice versa — no gaps."""
    monkeypatch.setenv("CF_API_TOKEN", "test-token")
    _mock_zones([
        {"id": "z1", "name": "drjones.com", "status": "active"},
        {"id": "z2", "name": "smithderm.com", "status": "active"},
    ])
    result = await skill.run()
    assert result.status == SkillStatus.SUCCESS
    assert result.data["wpe_only"] == []
    assert result.data["cf_only"] == []
    assert sorted(result.data["on_both"]) == ["drjones.com", "smithderm.com"]


# --- Test 2: WPE sites not on CF ---

@pytest.mark.asyncio
@respx.mock
async def test_wpe_only(skill, config_dir, monkeypatch):
    """Sites in sites.json but missing from CF zones."""
    monkeypatch.setenv("CF_API_TOKEN", "test-token")
    _mock_zones([
        {"id": "z1", "name": "drjones.com", "status": "active"},
        # smithderm.com is NOT in CF
    ])
    result = await skill.run()
    assert result.status == SkillStatus.SUCCESS
    assert "smithderm.com" in result.data["wpe_only"]
    assert result.data["cf_only"] == []


# --- Test 3: CF zones not in inventory ---

@pytest.mark.asyncio
@respx.mock
async def test_cf_only(skill, config_dir, monkeypatch):
    """CF zones that don't appear in sites.json."""
    monkeypatch.setenv("CF_API_TOKEN", "test-token")
    _mock_zones([
        {"id": "z1", "name": "drjones.com", "status": "active"},
        {"id": "z2", "name": "smithderm.com", "status": "active"},
        {"id": "z3", "name": "orphan-cf-zone.com", "status": "active"},
    ])
    result = await skill.run()
    assert result.status == SkillStatus.SUCCESS
    assert "orphan-cf-zone.com" in result.data["cf_only"]
    assert result.data["wpe_only"] == []


# --- Test 4: Both gaps present ---

@pytest.mark.asyncio
@respx.mock
async def test_both_gaps(skill, config_dir, monkeypatch):
    """Some WPE sites missing from CF and some CF zones missing from inventory."""
    monkeypatch.setenv("CF_API_TOKEN", "test-token")
    _mock_zones([
        {"id": "z1", "name": "drjones.com", "status": "active"},
        # smithderm.com missing from CF → wpe_only
        {"id": "z3", "name": "mystery-zone.com", "status": "active"},
        # mystery-zone.com not in sites.json → cf_only
    ])
    result = await skill.run()
    assert result.status == SkillStatus.SUCCESS
    assert "smithderm.com" in result.data["wpe_only"]
    assert "mystery-zone.com" in result.data["cf_only"]


# --- Test 5: Empty data ---

@pytest.mark.asyncio
@respx.mock
async def test_empty_data(skill, tmp_path, monkeypatch):
    """No sites in inventory and no CF zones — success with zeros."""
    monkeypatch.setenv("CF_API_TOKEN", "test-token")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sites.json").write_text('{"sites": []}')
    (data_dir / "accounts.json").write_text('{"wpengine": {}}')
    monkeypatch.chdir(tmp_path)
    _mock_zones([])
    result = await skill.run()
    assert result.status == SkillStatus.SUCCESS
    assert result.data["total_wpe"] == 0
    assert result.data["total_cf"] == 0
    assert result.data["on_both"] == []
    assert result.data["wpe_only"] == []
    assert result.data["cf_only"] == []
