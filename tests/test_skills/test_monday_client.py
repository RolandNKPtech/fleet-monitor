# tests/test_skills/test_monday_client.py
import pytest
import httpx
import respx
from skills.monday.client import MondayClient


@pytest.fixture
def client():
    return MondayClient(api_token="monday-test-token")


@pytest.mark.asyncio
@respx.mock
async def test_query_success(client):
    respx.post("https://api.monday.com/v2").mock(
        return_value=httpx.Response(200, json={
            "data": {"boards": [{"items_page": {"items": []}}]}
        })
    )
    result = await client.query("{ boards { items_page { items { name } } } }")
    assert "data" in result


@pytest.mark.asyncio
@respx.mock
async def test_auth_header(client):
    route = respx.post("https://api.monday.com/v2").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    await client.query("{ me { name } }")
    assert route.calls[0].request.headers["authorization"] == "monday-test-token"


@pytest.mark.asyncio
@respx.mock
async def test_api_error(client):
    respx.post("https://api.monday.com/v2").mock(
        return_value=httpx.Response(500, json={"errors": ["Internal error"]})
    )
    from core.errors import APIError
    with pytest.raises(APIError, match="monday"):
        await client.query("{ bad }")


@pytest.mark.asyncio
@respx.mock
async def test_network_error(client):
    respx.post("https://api.monday.com/v2").mock(side_effect=httpx.ConnectError("refused"))
    from core.errors import APIError
    with pytest.raises(APIError, match="monday"):
        await client.query("{ me { name } }")
