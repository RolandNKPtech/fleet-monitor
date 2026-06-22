import pytest
import respx
import httpx
from skills.cloudflare.purge_cache import PurgeCacheSkill
from skills.cloudflare.client import _reset_client
from skills.base import SkillStatus


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return PurgeCacheSkill()


def _mock_zone(domain="example.com", zone_id="zone_abc"):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": domain}).mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [{"id": zone_id, "name": domain}],
            "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1},
        })
    )


def _mock_purge(zone_id="zone_abc", status_code=200):
    respx.post(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache").mock(
        return_value=httpx.Response(status_code, json={
            "success": True,
            "result": {"id": zone_id},
            "errors": [],
            "messages": [],
        })
    )


@pytest.mark.asyncio
@respx.mock
async def test_purge_everything(skill, monkeypatch):
    """Purge everything with no URLs → SUCCESS, purge_everything=True."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    purge_route = respx.post("https://api.cloudflare.com/client/v4/zones/zone_abc/purge_cache").mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"id": "zone_abc"}, "errors": [], "messages": []})
    )

    result = await skill.run(target="example.com")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["purge_everything"] is True
    assert result.data["urls"] == []
    sent_body = purge_route.calls[0].request
    import json
    body = json.loads(sent_body.content)
    assert body == {"purge_everything": True}


@pytest.mark.asyncio
@respx.mock
async def test_purge_by_urls(skill, monkeypatch):
    """Purge with specific URLs → SUCCESS, correct body with files list."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    purge_route = respx.post("https://api.cloudflare.com/client/v4/zones/zone_abc/purge_cache").mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"id": "zone_abc"}, "errors": [], "messages": []})
    )

    urls = ["https://example.com/page1", "https://example.com/page2"]
    result = await skill.run(target="example.com", urls=urls)

    assert result.status == SkillStatus.SUCCESS
    assert result.data["purge_everything"] is False
    assert result.data["urls"] == urls

    import json
    body = json.loads(purge_route.calls[0].request.content)
    assert body == {"files": urls}


@pytest.mark.asyncio
@respx.mock
async def test_zone_not_found(skill, monkeypatch):
    """Zone lookup returns empty result → FAILURE."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": "missing.com"}).mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [],
            "result_info": {"page": 1, "per_page": 50, "count": 0, "total_count": 0, "total_pages": 1},
        })
    )

    result = await skill.run(target="missing.com")

    assert result.status == SkillStatus.FAILURE
    assert result.errors


@pytest.mark.asyncio
@respx.mock
async def test_api_error_on_purge(skill, monkeypatch):
    """Purge endpoint returns API error → FAILURE."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.post("https://api.cloudflare.com/client/v4/zones/zone_abc/purge_cache").mock(
        return_value=httpx.Response(200, json={
            "success": False,
            "result": None,
            "errors": [{"code": 1000, "message": "Purge failed due to internal error"}],
            "messages": [],
        })
    )

    result = await skill.run(target="example.com")

    assert result.status == SkillStatus.FAILURE
    assert result.errors
