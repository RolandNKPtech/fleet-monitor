import pytest
import json
import respx
import httpx
from skills.cloudflare.zone_inventory import ZoneInventorySkill
from skills.cloudflare.client import CloudflareClient, _reset_client
from skills.base import SkillStatus


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return ZoneInventorySkill()


def _mock_zones(zones, total_pages=1):
    """Helper to mock zone list response."""
    respx.get("https://api.cloudflare.com/client/v4/zones").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": zones,
            "result_info": {"page": 1, "per_page": 50, "count": len(zones), "total_count": len(zones), "total_pages": total_pages}
        })
    )


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Create a temp config with test sites."""
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


@pytest.mark.asyncio
@respx.mock
async def test_all_zones_match(skill, config_dir, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zones([
        {"id": "z1", "name": "drjones.com", "status": "active"},
        {"id": "z2", "name": "smithderm.com", "status": "active"},
    ])
    result = await skill.run()
    assert result.status == SkillStatus.SUCCESS
    assert result.data["total_zones"] == 2
    assert result.data["not_in_inventory"] == 0
    assert result.data["missing_from_cf"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_cf_has_extra_zones(skill, config_dir, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zones([
        {"id": "z1", "name": "drjones.com", "status": "active"},
        {"id": "z2", "name": "smithderm.com", "status": "active"},
        {"id": "z3", "name": "unknown.com", "status": "active"},
    ])
    result = await skill.run()
    assert result.data["not_in_inventory"] == 1
    assert "unknown.com" in result.data["not_in_inventory_domains"]


@pytest.mark.asyncio
@respx.mock
async def test_inventory_missing_from_cf(skill, config_dir, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zones([{"id": "z1", "name": "drjones.com", "status": "active"}])
    result = await skill.run()
    assert result.data["missing_from_cf"] == 1
    assert "smithderm.com" in result.data["missing_from_cf_domains"]


@pytest.mark.asyncio
@respx.mock
async def test_filter_by_account(skill, config_dir, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    # Add a site for different account
    sites = {"sites": [
        {"domain": "drjones.com", "wpe_account": "acctA", "active": True},
        {"domain": "other.com", "wpe_account": "acctB", "active": True},
    ]}
    (config_dir / "data" / "sites.json").write_text(json.dumps(sites))
    _mock_zones([
        {"id": "z1", "name": "drjones.com", "status": "active"},
        {"id": "z2", "name": "other.com", "status": "active"},
    ])
    result = await skill.run(account="acctA")
    # Only acctA sites checked against CF
    assert result.data["in_inventory"] == 1
    assert result.data["missing_from_cf"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_empty_zones(skill, config_dir, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zones([])
    result = await skill.run()
    assert result.status == SkillStatus.SUCCESS
    assert result.data["total_zones"] == 0
