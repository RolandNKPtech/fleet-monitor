# tests/test_skills/test_mainwp_client.py
import pytest
import httpx
import respx
from skills.wordpress.mainwp_client import MainWPClient, get_mainwp_client, _reset_client
from core.errors import APIError


DASH = "https://mainwp.example.com"
BASE_V2 = f"{DASH}/wp-json/mainwp/v2"
BASE_V1 = f"{DASH}/wp-json/mainwp/v1"


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def client():
    return MainWPClient(dashboard_url=DASH, api_key="test-key-123")


# --- Auth ---

@pytest.mark.asyncio
@respx.mock
async def test_bearer_auth_header(client):
    route = respx.get(f"{BASE_V2}/sites").mock(
        return_value=httpx.Response(200, json=[])
    )
    await client.get("/sites")
    assert route.calls[0].request.headers["authorization"] == "Bearer test-key-123"


@pytest.mark.asyncio
@respx.mock
async def test_auth_failure(client):
    respx.get(f"{BASE_V2}/sites").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    with pytest.raises(APIError, match="mainwp"):
        await client.get("/sites")


# --- Requests ---

@pytest.mark.asyncio
@respx.mock
async def test_get_success(client):
    from tests.conftest import load_fixture
    respx.get(f"{BASE_V2}/sites").mock(
        return_value=httpx.Response(200, json=load_fixture("mainwp_sites"))
    )
    result = await client.get("/sites")
    assert len(result) == 2


@pytest.mark.asyncio
@respx.mock
async def test_get_paginated(client):
    respx.get(f"{BASE_V2}/sites").mock(side_effect=[
        httpx.Response(200, json=[{"id": 1}, {"id": 2}]),
        httpx.Response(200, json=[{"id": 3}]),
        httpx.Response(200, json=[]),
    ])
    results = await client.get_paginated("/sites", per_page=2)
    assert len(results) == 3


# --- v2 to v1 Fallback ---

@pytest.mark.asyncio
@respx.mock
async def test_v2_to_v1_fallback(client):
    respx.get(f"{BASE_V2}/sites").mock(
        return_value=httpx.Response(404, json={"message": "Not found"})
    )
    respx.get(f"{BASE_V1}/sites").mock(
        return_value=httpx.Response(200, json=[{"id": 1}])
    )
    result = await client.get("/sites")
    assert len(result) == 1


# --- Network Error ---

@pytest.mark.asyncio
@respx.mock
async def test_network_error(client):
    respx.get(f"{BASE_V2}/sites").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(APIError, match="mainwp"):
        await client.get("/sites")


# --- Graceful None ---

def test_no_config_returns_none(monkeypatch):
    monkeypatch.delenv("MAINWP_URL", raising=False)
    monkeypatch.delenv("MAINWP_API_KEY", raising=False)
    client = get_mainwp_client()
    assert client is None


# --- Singleton ---

def test_singleton(monkeypatch):
    monkeypatch.setenv("MAINWP_URL", DASH)
    monkeypatch.setenv("MAINWP_API_KEY", "key")
    c1 = get_mainwp_client()
    c2 = get_mainwp_client()
    assert c1 is c2
