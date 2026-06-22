import pytest
import respx
import httpx
from skills.cloudflare.dns_check import DnsCheckSkill
from skills.cloudflare.client import _reset_client
from skills.base import SkillStatus
from tests.conftest import load_fixture


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return DnsCheckSkill()


def _mock_zone(domain="drjones.com", zone_id="zone_001"):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": domain}).mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": [{"id": zone_id, "name": domain}],
            "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1}
        })
    )


def _mock_dns(zone_id, fixture_name):
    respx.get(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records").mock(
        return_value=httpx.Response(200, json=load_fixture(fixture_name))
    )


@pytest.mark.asyncio
@respx.mock
async def test_correct_o2o_dns(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    _mock_dns("zone_001", "cf_dns_records_o2o")
    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.SUCCESS
    assert result.data["issues"] == []


@pytest.mark.asyncio
@respx.mock
async def test_wrong_cname_target(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("smithderm.com", "zone_002")
    _mock_dns("zone_002", "cf_dns_records_wrong")
    result = await skill.run(target="smithderm.com")
    assert result.status == SkillStatus.WARNING
    assert any("points to" in i for i in result.data["issues"])


@pytest.mark.asyncio
@respx.mock
async def test_unproxied_www(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("smithderm.com", "zone_002")
    _mock_dns("zone_002", "cf_dns_records_wrong")
    result = await skill.run(target="smithderm.com")
    assert any("not proxied" in i for i in result.data["issues"])


@pytest.mark.asyncio
@respx.mock
async def test_missing_www(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/dns_records").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [{"id": "r1", "type": "A", "name": "drjones.com", "content": "1.2.3.4", "proxied": False}]
        })
    )
    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.WARNING
    assert any("No www" in i for i in result.data["issues"])


@pytest.mark.asyncio
@respx.mock
async def test_a_record_instead_of_cname(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/dns_records").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [{"id": "r1", "type": "A", "name": "www.drjones.com", "content": "1.2.3.4", "proxied": True}]
        })
    )
    result = await skill.run(target="drjones.com")
    assert any("expected CNAME" in i for i in result.data["issues"])


@pytest.mark.asyncio
@respx.mock
async def test_multiple_records_reported(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    _mock_dns("zone_001", "cf_dns_records_o2o")
    result = await skill.run(target="drjones.com")
    assert len(result.data["records"]) == 2


@pytest.mark.asyncio
@respx.mock
async def test_zone_not_found(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": "nope.com"}).mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": [],
            "result_info": {"page": 1, "per_page": 50, "count": 0, "total_count": 0, "total_pages": 1}
        })
    )
    result = await skill.run(target="nope.com")
    assert result.status == SkillStatus.FAILURE
