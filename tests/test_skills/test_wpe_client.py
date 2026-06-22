import pytest
import httpx
import respx
from skills.wpengine.client import WPEngineClient, get_wpe_client, _reset_client
from core.errors import APIError


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def client():
    return WPEngineClient(username="test-user", password="test-pass")


BASE = "https://api.wpengineapi.com/v1"


# --- Auth ---

@pytest.mark.asyncio
@respx.mock
async def test_basic_auth_header(client):
    import base64
    route = respx.get(f"{BASE}/user").mock(
        return_value=httpx.Response(200, json={"id": "u1", "email": "test@test.com"})
    )
    await client.get("/user")
    auth = route.calls[0].request.headers["authorization"]
    expected = "Basic " + base64.b64encode(b"test-user:test-pass").decode()
    assert auth == expected


@pytest.mark.asyncio
@respx.mock
async def test_auth_failure_401(client):
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(401, json={"message": "Invalid credentials"})
    )
    with pytest.raises(APIError, match="wpengine"):
        await client.get("/installs")


# --- Pagination ---

@pytest.mark.asyncio
@respx.mock
async def test_get_paginated_single_page(client):
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={
            "results": [{"id": "i1", "name": "site1"}],
            "count": 1
        })
    )
    results = await client.get_paginated("/installs")
    assert len(results) == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_paginated_multi_page(client):
    respx.get(f"{BASE}/installs", params__contains={"offset": "0"}).mock(
        return_value=httpx.Response(200, json={
            "results": [{"id": f"i{i}", "name": f"site{i}"} for i in range(100)],
            "count": 150
        })
    )
    respx.get(f"{BASE}/installs", params__contains={"offset": "100"}).mock(
        return_value=httpx.Response(200, json={
            "results": [{"id": f"i{i}", "name": f"site{i}"} for i in range(100, 150)],
            "count": 150
        })
    )
    results = await client.get_paginated("/installs")
    assert len(results) == 150


@pytest.mark.asyncio
@respx.mock
async def test_get_paginated_empty(client):
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={"results": [], "count": 0})
    )
    results = await client.get_paginated("/installs")
    assert results == []


# --- Rate Limiting ---

@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_429(client):
    call_count = 0
    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "1", "X-RateLimit-Remaining": "0"})
        return httpx.Response(200, json={"results": [], "count": 0})
    respx.get(f"{BASE}/installs").mock(side_effect=side_effect)
    result = await client.get("/installs")
    assert result is not None
    assert call_count == 2


# --- Server Errors ---

@pytest.mark.asyncio
@respx.mock
async def test_server_error_retries(client):
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(500, json={"message": "Internal error"})
    )
    with pytest.raises(APIError, match="wpengine"):
        await client.get("/installs")


# --- Network Errors ---

@pytest.mark.asyncio
@respx.mock
async def test_network_timeout(client):
    respx.get(f"{BASE}/installs").mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(APIError, match="wpengine"):
        await client.get("/installs")


# --- Install Helpers ---

@pytest.mark.asyncio
@respx.mock
async def test_get_installs(client):
    from tests.conftest import load_fixture
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=load_fixture("wpe_installs"))
    )
    installs = await client.get_installs()
    assert len(installs) == 2
    assert installs[0]["name"] == "drjones"


@pytest.mark.asyncio
@respx.mock
async def test_get_install_by_name(client):
    from tests.conftest import load_fixture
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json=load_fixture("wpe_installs"))
    )
    install = await client.get_install_by_name("drjones")
    assert install is not None
    assert install["id"] == "inst_001"


@pytest.mark.asyncio
@respx.mock
async def test_get_install_by_name_not_found(client):
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={"results": [], "count": 0})
    )
    install = await client.get_install_by_name("nonexistent")
    assert install is None


# --- Singleton ---

def test_singleton(monkeypatch):
    monkeypatch.setenv("WPE_API_USER", "u")
    monkeypatch.setenv("WPE_API_PASSWORD", "p")
    c1 = get_wpe_client()
    c2 = get_wpe_client()
    assert c1 is c2
