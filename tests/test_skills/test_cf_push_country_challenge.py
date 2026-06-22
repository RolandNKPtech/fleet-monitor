import json
import pytest
import respx
import httpx

from skills.cloudflare.push_country_challenge import (
    PushCountryChallengeSkill,
    RULE_DESCRIPTION,
    STANDARD_EXPRESSION,
)
from skills.cloudflare.client import _reset_client
from skills.base import SkillStatus


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return PushCountryChallengeSkill()


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


def _mock_ruleset(zone_id="zone_001", ruleset_id="rs_001", rules=None):
    rules = rules or []
    respx.get(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/rulesets/phases/http_request_firewall_custom/entrypoint"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "result": {"id": ruleset_id, "phase": "http_request_firewall_custom", "rules": rules},
            },
        )
    )


def _mock_geo_us_dominant(country_counts=None):
    """Mock CF GraphQL to return US-dominant traffic (95% by default).
    Pass {} to mock no-traffic scenario (use is None check to allow empty dict)."""
    if country_counts is None:
        country_counts = {"US": 9500, "CA": 500}
    groups = [
        {"sum": {"requests": c}, "dimensions": {"clientCountryName": k}}
        for k, c in country_counts.items()
    ]
    respx.post("https://api.cloudflare.com/client/v4/graphql").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"viewer": {"zones": [{"httpRequestsAdaptiveGroups": groups}]}}},
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_when_missing(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_ruleset(rules=[])

    result = await skill.run(target="drjones.com", mode="dry_run")
    assert result.status == SkillStatus.SUCCESS
    assert result.data["already_present"] is False
    assert result.data["would_add"] is True


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_when_present(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    existing = {
        "id": "rule_country_01",
        "expression": STANDARD_EXPRESSION,
        "action": "managed_challenge",
        "description": RULE_DESCRIPTION,
        "enabled": True,
    }
    _mock_ruleset(rules=[existing])

    result = await skill.run(target="drjones.com", mode="dry_run")
    assert result.data["already_present"] is True
    assert result.data["would_add"] is False


@pytest.mark.asyncio
@respx.mock
async def test_apply_creates_rule(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_geo_us_dominant()  # apply triggers pre-check
    _mock_ruleset(rules=[])

    post_route = respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "result": {"id": "rs_001", "rules": [
                {"id": "rule_new_01", "action": "managed_challenge", "description": RULE_DESCRIPTION},
            ]}},
        )
    )

    result = await skill.run(target="drjones.com", mode="apply", geography_days=1)
    assert result.status == SkillStatus.SUCCESS
    assert result.data["action_taken"] == "added"
    assert post_route.called
    body = json.loads(post_route.calls[0].request.content)
    assert body["action"] == "managed_challenge"
    assert body["expression"] == STANDARD_EXPRESSION
    assert body["description"] == RULE_DESCRIPTION


@pytest.mark.asyncio
@respx.mock
async def test_apply_idempotent_when_present(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_geo_us_dominant()
    existing = {
        "id": "rule_country_01",
        "expression": STANDARD_EXPRESSION,
        "action": "managed_challenge",
        "description": RULE_DESCRIPTION,
        "enabled": True,
    }
    _mock_ruleset(rules=[existing])

    post_route = respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(return_value=httpx.Response(200, json={"success": True, "result": {}}))

    result = await skill.run(target="drjones.com", mode="apply", geography_days=1)
    assert result.status == SkillStatus.SUCCESS
    assert result.data["action_taken"] == "already_present"
    assert not post_route.called


@pytest.mark.asyncio
@respx.mock
async def test_invalid_mode(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)

    result = await skill.run(target="drjones.com", mode="bogus")
    assert result.status == SkillStatus.FAILURE
    assert "mode" in result.message.lower()


@pytest.mark.asyncio
@respx.mock
async def test_changelog_written_on_apply(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    _mock_zone()
    _mock_geo_us_dominant()
    _mock_ruleset(rules=[])
    respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(return_value=httpx.Response(200, json={"success": True, "result": {"rules": [{"id": "r1", "description": RULE_DESCRIPTION}]}}))

    await skill.run(target="drjones.com", mode="apply", geography_days=1)

    log_files = list((tmp_path / "data" / "reports").glob("cf-rule-changes-*.jsonl"))
    assert len(log_files) == 1
    entry = json.loads(log_files[0].read_text().strip().split("\n")[-1])
    assert entry["domain"] == "drjones.com"
    assert entry["action"] == "added"


@pytest.mark.asyncio
@respx.mock
async def test_apply_skipped_when_international(skill, monkeypatch, tmp_path):
    """Site with significant non-US traffic must NOT get the country challenge."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_geo_us_dominant({"US": 6000, "AE": 2000, "SA": 2000})  # 60% US

    post_route = respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(return_value=httpx.Response(200, json={"success": True, "result": {}}))

    result = await skill.run(target="drjones.com", mode="apply", geography_days=1)

    assert result.status == SkillStatus.SKIPPED
    assert result.data["action_taken"] == "skipped_geography"
    assert result.data["geography"]["us_pct"] == 60.0
    assert not post_route.called  # never even hit the rule API


@pytest.mark.asyncio
@respx.mock
async def test_apply_skipped_when_no_traffic_data(skill, monkeypatch, tmp_path):
    """Site with no traffic data: skip — safer than blocking unknown international."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_geo_us_dominant({})  # empty traffic

    result = await skill.run(target="drjones.com", mode="apply", geography_days=1)

    assert result.status == SkillStatus.SKIPPED
    assert result.data["action_taken"] == "skipped_geography"


@pytest.mark.asyncio
@respx.mock
async def test_apply_pre_check_disabled(skill, monkeypatch, tmp_path):
    """pre_check_geography=False: proceeds without checking traffic."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_ruleset(rules=[])
    # No geo mock — would fail if pre-check ran

    post_route = respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(return_value=httpx.Response(200, json={"success": True, "result": {"rules": [{"id": "r1", "description": RULE_DESCRIPTION}]}}))

    result = await skill.run(target="drjones.com", mode="apply", pre_check_geography=False)

    assert result.status == SkillStatus.SUCCESS
    assert result.data["action_taken"] == "added"
    assert post_route.called
    assert result.data["geography"] is None  # pre-check was skipped
