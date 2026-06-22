# tests/test_skills/test_cf_resolve.py
import pytest
import json
import respx
import httpx
from skills.cloudflare._resolve import resolve_targets
from skills.cloudflare.client import CloudflareClient


@pytest.fixture
def client():
    return CloudflareClient(api_token="test-token")


@pytest.fixture
def sites_json(tmp_path):
    sites = {
        "sites": [
            {"domain": "drjones.com", "wpe_account": "acctA", "active": True},
            {"domain": "smithderm.com", "wpe_account": "acctA", "active": True},
            {"domain": "medspa.com", "wpe_account": "acctB", "active": True},
            {"domain": "inactive.com", "wpe_account": "acctA", "active": False},
        ]
    }
    path = tmp_path / "data" / "sites.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(sites))
    return tmp_path


@pytest.mark.asyncio
@respx.mock
async def test_resolve_single_domain(client, sites_json):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": "drjones.com"}).mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": [{"id": "z1", "name": "drjones.com"}],
            "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1}
        })
    )
    targets = await resolve_targets("drjones.com", client, root_dir=sites_json)
    assert len(targets) == 1
    assert targets[0] == {"domain": "drjones.com", "zone_id": "z1"}


@pytest.mark.asyncio
@respx.mock
async def test_resolve_account(client, sites_json):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": "drjones.com"}).mock(
        return_value=httpx.Response(200, json={"success": True, "result": [{"id": "z1", "name": "drjones.com"}], "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1}})
    )
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": "smithderm.com"}).mock(
        return_value=httpx.Response(200, json={"success": True, "result": [{"id": "z2", "name": "smithderm.com"}], "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1}})
    )
    targets = await resolve_targets("acctA", client, root_dir=sites_json)
    assert len(targets) == 2
    domains = [t["domain"] for t in targets]
    assert "drjones.com" in domains
    assert "smithderm.com" in domains
    assert "inactive.com" not in domains


@pytest.mark.asyncio
@respx.mock
async def test_resolve_all(client, sites_json):
    for domain, zid in [("drjones.com", "z1"), ("smithderm.com", "z2"), ("medspa.com", "z3")]:
        respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": domain}).mock(
            return_value=httpx.Response(200, json={"success": True, "result": [{"id": zid, "name": domain}], "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1}})
        )
    targets = await resolve_targets("all", client, root_dir=sites_json)
    assert len(targets) == 3


@pytest.mark.asyncio
@respx.mock
async def test_resolve_unknown_account(client, sites_json):
    targets = await resolve_targets("nkpmedical99", client, root_dir=sites_json)
    assert targets == []


@pytest.mark.asyncio
@respx.mock
async def test_resolve_domain_zone_not_found(client, sites_json):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": "missing.com"}).mock(
        return_value=httpx.Response(200, json={"success": True, "result": [], "result_info": {"page": 1, "per_page": 50, "count": 0, "total_count": 0, "total_pages": 1}})
    )
    from core.errors import APIError
    with pytest.raises(APIError, match="not found"):
        await resolve_targets("missing.com", client, root_dir=sites_json)
