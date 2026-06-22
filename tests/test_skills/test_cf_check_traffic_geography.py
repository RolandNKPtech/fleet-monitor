import pytest
import respx
import httpx

from skills.cloudflare.check_traffic_geography import CheckTrafficGeographySkill
from skills.cloudflare.client import _reset_client
from skills.base import SkillStatus


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return CheckTrafficGeographySkill()


def _mock_zone(domain="drjones.com", zone_id="zone_001"):
    respx.get(
        "https://api.cloudflare.com/client/v4/zones",
        params__contains={"name": domain},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "result": [{"id": zone_id, "name": domain}],
                "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1},
            },
        )
    )


def _mock_graphql(country_counts: dict[str, int]):
    """country_counts: {'US': 9500, 'CA': 300, 'MX': 200}"""
    groups = [
        {"sum": {"requests": count}, "dimensions": {"clientCountryName": country}}
        for country, count in country_counts.items()
    ]
    respx.post("https://api.cloudflare.com/client/v4/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "viewer": {
                        "zones": [{"httpRequestsAdaptiveGroups": groups}]
                    }
                }
            },
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_us_dominant_site(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    _mock_graphql({"US": 9500, "CA": 300, "MX": 200})

    result = await skill.run(target="drjones.com", days=1)

    assert result.status == SkillStatus.SUCCESS
    assert result.data["us_pct"] == 95.0
    assert result.data["is_us_dominant"] is True
    assert result.data["total_requests"] == 10000
    assert result.data["top_countries"][0] == {"country": "US", "count": 9500, "pct": 95.0}


@pytest.mark.asyncio
@respx.mock
async def test_international_site_below_threshold(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    # 70% US — below default 90% threshold
    _mock_graphql({"US": 7000, "AE": 1500, "SA": 1000, "GB": 500})

    result = await skill.run(target="drjones.com", days=1)

    assert result.status == SkillStatus.WARNING
    assert result.data["us_pct"] == 70.0
    assert result.data["is_us_dominant"] is False


@pytest.mark.asyncio
@respx.mock
async def test_custom_threshold(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    _mock_graphql({"US": 8500, "CA": 1500})  # 85% US

    result = await skill.run(target="drjones.com", days=1, min_us_pct=80.0)
    assert result.status == SkillStatus.SUCCESS
    assert result.data["is_us_dominant"] is True

    _reset_client()
    _mock_zone()
    _mock_graphql({"US": 8500, "CA": 1500})
    result2 = await skill.run(target="drjones.com", days=1, min_us_pct=95.0)
    assert result2.status == SkillStatus.WARNING
    assert result2.data["is_us_dominant"] is False


@pytest.mark.asyncio
@respx.mock
async def test_no_traffic_data(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    _mock_graphql({})  # No traffic at all

    result = await skill.run(target="drjones.com", days=1)

    assert result.status == SkillStatus.WARNING
    assert result.data["total_requests"] == 0
    assert result.data["is_us_dominant"] is False
    assert result.data["us_pct"] is None
    assert "no traffic" in result.message.lower() or "no data" in result.message.lower()


@pytest.mark.asyncio
@respx.mock
async def test_graphql_error_returns_failure(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.post("https://api.cloudflare.com/client/v4/graphql").mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "rate limited"}]})
    )

    result = await skill.run(target="drjones.com", days=1)
    assert result.status == SkillStatus.FAILURE
