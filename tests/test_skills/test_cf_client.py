import pytest
import httpx
import respx
from skills.cloudflare.client import CloudflareClient, get_cf_client
from core.errors import APIError, ConfigError


@pytest.fixture
def client():
    return CloudflareClient(api_token="test-token-123")


# --- Auth ---

@pytest.mark.asyncio
@respx.mock
async def test_auth_header_sent(client):
    route = respx.get("https://api.cloudflare.com/client/v4/zones").mock(
        return_value=httpx.Response(200, json={"success": True, "result": [], "result_info": {"page": 1, "per_page": 50, "count": 0, "total_count": 0, "total_pages": 1}})
    )
    await client.get("/zones")
    assert route.calls[0].request.headers["authorization"] == "Bearer test-token-123"


@pytest.mark.asyncio
@respx.mock
async def test_auth_failure_401(client):
    respx.get("https://api.cloudflare.com/client/v4/zones").mock(
        return_value=httpx.Response(401, json={"success": False, "errors": [{"message": "Invalid API Token"}]})
    )
    with pytest.raises(APIError, match="cloudflare"):
        await client.get("/zones")


@pytest.mark.asyncio
@respx.mock
async def test_auth_forbidden_403(client):
    respx.get("https://api.cloudflare.com/client/v4/zones").mock(
        return_value=httpx.Response(403, json={"success": False, "errors": [{"message": "Forbidden"}]})
    )
    with pytest.raises(APIError, match="cloudflare"):
        await client.get("/zones")


# --- Pagination ---

@pytest.mark.asyncio
@respx.mock
async def test_get_paginated_single_page(client):
    respx.get("https://api.cloudflare.com/client/v4/zones").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [{"id": "z1", "name": "a.com"}],
            "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1}
        })
    )
    results = await client.get_paginated("/zones")
    assert len(results) == 1
    assert results[0]["name"] == "a.com"


@pytest.mark.asyncio
@respx.mock
async def test_get_paginated_multi_page(client):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"page": "1"}).mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [{"id": "z1", "name": "a.com"}, {"id": "z2", "name": "b.com"}],
            "result_info": {"page": 1, "per_page": 2, "count": 2, "total_count": 3, "total_pages": 2}
        })
    )
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"page": "2"}).mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [{"id": "z3", "name": "c.com"}],
            "result_info": {"page": 2, "per_page": 2, "count": 1, "total_count": 3, "total_pages": 2}
        })
    )
    results = await client.get_paginated("/zones", per_page=2)
    assert len(results) == 3


@pytest.mark.asyncio
@respx.mock
async def test_get_paginated_empty(client):
    respx.get("https://api.cloudflare.com/client/v4/zones").mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": [],
            "result_info": {"page": 1, "per_page": 50, "count": 0, "total_count": 0, "total_pages": 1}
        })
    )
    results = await client.get_paginated("/zones")
    assert results == []


# --- Rate Limiting ---

@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_429_retries(client):
    call_count = 0
    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"success": False})
        return httpx.Response(200, json={"success": True, "result": [{"id": "z1"}], "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1}})

    respx.get("https://api.cloudflare.com/client/v4/zones").mock(side_effect=side_effect)
    result = await client.get("/zones")
    assert result["success"] is True
    assert call_count == 2


# --- Server Errors ---

@pytest.mark.asyncio
@respx.mock
async def test_server_error_retries_then_fails(client):
    respx.get("https://api.cloudflare.com/client/v4/zones").mock(
        return_value=httpx.Response(500, json={"success": False, "errors": [{"message": "Internal"}]})
    )
    with pytest.raises(APIError, match="cloudflare"):
        await client.get("/zones")


# --- Network Errors ---

@pytest.mark.asyncio
@respx.mock
async def test_network_timeout(client):
    respx.get("https://api.cloudflare.com/client/v4/zones").mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(APIError, match="cloudflare"):
        await client.get("/zones")


@pytest.mark.asyncio
@respx.mock
async def test_connection_refused(client):
    respx.get("https://api.cloudflare.com/client/v4/zones").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(APIError, match="cloudflare"):
        await client.get("/zones")


# --- Zone ID Cache ---

@pytest.mark.asyncio
@respx.mock
async def test_get_zone_id_caches(client):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": "drjones.com"}).mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": [{"id": "zone_001", "name": "drjones.com"}],
            "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1}
        })
    )
    id1 = await client.get_zone_id("drjones.com")
    id2 = await client.get_zone_id("drjones.com")
    assert id1 == "zone_001"
    assert id2 == "zone_001"
    # Only one API call made (second was cached)
    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_zone_id_not_found(client):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": "nope.com"}).mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": [],
            "result_info": {"page": 1, "per_page": 50, "count": 0, "total_count": 0, "total_pages": 1}
        })
    )
    with pytest.raises(APIError, match="not found"):
        await client.get_zone_id("nope.com")


# --- GraphQL ---

@pytest.mark.asyncio
@respx.mock
async def test_graphql_query(client):
    respx.post("https://api.cloudflare.com/client/v4/graphql").mock(
        return_value=httpx.Response(200, json={
            "data": {"viewer": {"zones": [{"httpRequests1dGroups": []}]}},
            "errors": None
        })
    )
    result = await client.graphql("query { viewer { zones { httpRequests1dGroups { sum { requests } } } } }")
    assert "data" in result


@pytest.mark.asyncio
@respx.mock
async def test_graphql_error(client):
    respx.post("https://api.cloudflare.com/client/v4/graphql").mock(
        return_value=httpx.Response(200, json={
            "data": None,
            "errors": [{"message": "Query too complex"}]
        })
    )
    with pytest.raises(APIError, match="GraphQL"):
        await client.graphql("query { bad }")


# --- Zone Settings ---

@pytest.mark.asyncio
@respx.mock
async def test_get_zone_settings(client):
    from tests.conftest import load_fixture
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/settings").mock(
        return_value=httpx.Response(200, json=load_fixture("cf_settings_compliant"))
    )
    settings = await client.get_zone_settings("zone_001")
    assert settings["ssl"] == "strict"
    assert settings["rocket_loader"] == "off"


# --- Singleton ---

def test_singleton_returns_same_instance():
    import os
    os.environ["CF_API_TOKEN"] = "test-token"
    from skills.cloudflare.client import _reset_client
    _reset_client()  # reset for test isolation
    c1 = get_cf_client()
    c2 = get_cf_client()
    assert c1 is c2
    _reset_client()
