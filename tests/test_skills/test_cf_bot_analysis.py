import pytest
import respx
import httpx
from unittest.mock import AsyncMock, patch

from skills.cloudflare.bot_analysis import BotAnalysisSkill
from skills.cloudflare.client import _reset_client
from skills.base import SkillStatus
from tests.conftest import load_fixture


ZONE_RESPONSE = {
    "success": True,
    "result": [{"id": "zone_001", "name": "drjones.com"}],
    "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1},
}

GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return BotAnalysisSkill()


def _mock_zone(domain="drjones.com", zone_id="zone_001"):
    respx.get(
        "https://api.cloudflare.com/client/v4/zones",
        params__contains={"name": domain},
    ).mock(return_value=httpx.Response(200, json=ZONE_RESPONSE))


# ---------------------------------------------------------------------------
# Test 1: Normal traffic with threats → correct percentages
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_normal_traffic_with_threats(skill, monkeypatch):
    """Traffic + security fixtures produce correct threat_percentage and cache_hit_rate."""
    monkeypatch.setenv("CF_API_TOKEN", "test")

    traffic_fixture = load_fixture("cf_graphql_traffic")
    security_fixture = load_fixture("cf_graphql_security")

    call_count = 0

    async def mock_graphql(self, query, variables=None):
        nonlocal call_count
        call_count += 1
        if "httpRequests1dGroups" in query:
            return traffic_fixture
        return security_fixture

    with patch("skills.cloudflare.client.CloudflareClient.graphql", new=mock_graphql), \
         patch("skills.cloudflare.client.CloudflareClient.get_zone_id", new=AsyncMock(return_value="zone_001")):
        result = await skill.run(target="drjones.com", days=7)

    assert result.status == SkillStatus.SUCCESS

    # Traffic fixture: 3 days of data
    # requests = 6500+6200+6800 = 19500, cached = 5200+4900+5500 = 15600, threats = 1800+1600+2000 = 5400
    assert result.data["total_requests"] == 19500
    assert result.data["total_cached"] == 15600
    assert result.data["total_threats"] == 5400
    assert result.data["threat_percentage"] == round((5400 / 19500) * 100, 2)
    assert result.data["cache_hit_rate"] == round((15600 / 19500) * 100, 2)

    # Security fixture is returned for each of 7 days, so counts are multiplied by 7
    # Per-day: CN=1200+300=1500, RU=800, IN=100 → over 7 days: CN=10500, RU=5600, IN=700
    top = result.data["top_countries"]
    assert top[0]["country"] == "CN"
    assert top[0]["count"] == 1500 * 7
    assert top[1]["country"] == "RU"
    assert top[1]["count"] == 800 * 7

    # Actions: managed_challenge=(1200+800)*7=14000, block=(300+100)*7=2800
    actions = {a["action"]: a["count"] for a in result.data["actions_breakdown"]}
    assert actions["managed_challenge"] == 2000 * 7
    assert actions["block"] == 400 * 7


# ---------------------------------------------------------------------------
# Test 2: Zero threats → SUCCESS, threat_percentage=0
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_zero_threats(skill, monkeypatch):
    """When traffic has zero threats, threat_percentage is 0 and status is SUCCESS."""
    monkeypatch.setenv("CF_API_TOKEN", "test")

    zero_traffic = {
        "data": {
            "viewer": {
                "zones": [{
                    "httpRequests1dGroups": [
                        {"dimensions": {"date": "2026-03-24"}, "sum": {"requests": 1000, "cachedRequests": 800, "threats": 0}},
                    ]
                }]
            }
        }
    }
    empty_security = load_fixture("cf_graphql_empty")
    # cf_graphql_empty has firewallEventsAdaptiveGroups key
    empty_sec = {"data": {"viewer": {"zones": [{"firewallEventsAdaptiveGroups": []}]}}}

    async def mock_graphql(self, query, variables=None):
        if "httpRequests1dGroups" in query:
            return zero_traffic
        return empty_sec

    with patch("skills.cloudflare.client.CloudflareClient.graphql", new=mock_graphql), \
         patch("skills.cloudflare.client.CloudflareClient.get_zone_id", new=AsyncMock(return_value="zone_001")):
        result = await skill.run(target="drjones.com", days=1)

    assert result.status == SkillStatus.SUCCESS
    assert result.data["threat_percentage"] == 0
    assert result.data["total_security_events"] == 0
    assert result.data["top_countries"] == []


# ---------------------------------------------------------------------------
# Test 3: 7-day query → verify multiple GraphQL calls issued (7 security + 1 traffic = 8)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_7day_issues_multiple_graphql_calls(skill, monkeypatch):
    """7 days → 1 traffic query + 7 security queries = 8 total graphql calls."""
    monkeypatch.setenv("CF_API_TOKEN", "test")

    traffic_fixture = load_fixture("cf_graphql_traffic")
    security_fixture = load_fixture("cf_graphql_security")
    graphql_calls = []

    async def mock_graphql(self, query, variables=None):
        graphql_calls.append(query)
        if "httpRequests1dGroups" in query:
            return traffic_fixture
        return security_fixture

    with patch("skills.cloudflare.client.CloudflareClient.graphql", new=mock_graphql), \
         patch("skills.cloudflare.client.CloudflareClient.get_zone_id", new=AsyncMock(return_value="zone_001")):
        result = await skill.run(target="drjones.com", days=7)

    assert result.status == SkillStatus.SUCCESS
    # 1 traffic + 7 security
    assert len(graphql_calls) == 8
    traffic_calls = [q for q in graphql_calls if "httpRequests1dGroups" in q]
    security_calls = [q for q in graphql_calls if "firewallEventsAdaptiveGroups" in q]
    assert len(traffic_calls) == 1
    assert len(security_calls) == 7


# ---------------------------------------------------------------------------
# Test 4: Single day (days=1) → 1 security query only
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_single_day_one_security_query(skill, monkeypatch):
    """days=1 → exactly 1 security GraphQL query issued."""
    monkeypatch.setenv("CF_API_TOKEN", "test")

    traffic_fixture = load_fixture("cf_graphql_traffic")
    security_fixture = load_fixture("cf_graphql_security")
    security_calls = []

    async def mock_graphql(self, query, variables=None):
        if "firewallEventsAdaptiveGroups" in query:
            security_calls.append(query)
            return security_fixture
        return traffic_fixture

    with patch("skills.cloudflare.client.CloudflareClient.graphql", new=mock_graphql), \
         patch("skills.cloudflare.client.CloudflareClient.get_zone_id", new=AsyncMock(return_value="zone_001")):
        result = await skill.run(target="drjones.com", days=1)

    assert result.status == SkillStatus.SUCCESS
    assert len(security_calls) == 1


# ---------------------------------------------------------------------------
# Test 5: GraphQL error → FAILURE
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_graphql_error_returns_failure(skill, monkeypatch):
    """When GraphQL raises APIError, skill returns FAILURE."""
    monkeypatch.setenv("CF_API_TOKEN", "test")

    from core.errors import APIError

    async def mock_graphql(self, query, variables=None):
        raise APIError("cloudflare", None, "GraphQL error: internal error")

    with patch("skills.cloudflare.client.CloudflareClient.graphql", new=mock_graphql), \
         patch("skills.cloudflare.client.CloudflareClient.get_zone_id", new=AsyncMock(return_value="zone_001")):
        result = await skill.run(target="drjones.com", days=7)

    assert result.status == SkillStatus.FAILURE
    assert len(result.errors) > 0
    assert "drjones.com" in result.message


# ---------------------------------------------------------------------------
# Test 6: Empty response → SUCCESS with zeros
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_response_returns_success_with_zeros(skill, monkeypatch):
    """Empty GraphQL responses produce SUCCESS with all-zero metrics."""
    monkeypatch.setenv("CF_API_TOKEN", "test")

    empty_traffic = {"data": {"viewer": {"zones": [{"httpRequests1dGroups": []}]}}}
    empty_security = {"data": {"viewer": {"zones": [{"firewallEventsAdaptiveGroups": []}]}}}

    async def mock_graphql(self, query, variables=None):
        if "httpRequests1dGroups" in query:
            return empty_traffic
        return empty_security

    with patch("skills.cloudflare.client.CloudflareClient.graphql", new=mock_graphql), \
         patch("skills.cloudflare.client.CloudflareClient.get_zone_id", new=AsyncMock(return_value="zone_001")):
        result = await skill.run(target="drjones.com", days=7)

    assert result.status == SkillStatus.SUCCESS
    assert result.data["total_requests"] == 0
    assert result.data["total_threats"] == 0
    assert result.data["threat_percentage"] == 0
    assert result.data["cache_hit_rate"] == 0
    assert result.data["total_security_events"] == 0
    assert result.data["top_countries"] == []
    assert result.data["actions_breakdown"] == []
