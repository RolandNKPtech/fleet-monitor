import pytest
import respx
import httpx

from skills.cloudflare.setup_o2o_waf import SetupO2oWafSkill
from skills.cloudflare.client import _reset_client
from skills.base import SkillStatus


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return SetupO2oWafSkill()


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


def _ruleset_response(rules):
    return {
        "success": True,
        "result": {"id": "rs_001", "phase": "http_request_firewall_custom", "rules": rules},
    }


def _mock_geo(country_counts=None):
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


COUNTRY_RULE = {
    "id": "rule_country_01",
    "expression": '(ip.src.country ne "US" and not cf.client.bot)',
    "action": "managed_challenge",
    "description": "Challenge Non-US Traffic",
    "enabled": True,
}
ALLOWLIST_RULE = {
    "id": "rule_allow_01",
    "expression": '(http.user_agent contains "Schema-Markup-Validator")',
    "action": "skip",
    "description": "NKP: validator + crawler allowlist",
    "enabled": True,
}


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_empty_zone(skill, monkeypatch, tmp_path):
    """Empty zone on dry_run: composer reports WARNING because audit flags both rules missing."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    respx.get(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint"
    ).mock(return_value=httpx.Response(200, json=_ruleset_response([])))

    result = await skill.run(target="drjones.com", mode="dry_run")

    # Audit returns WARNING because both rules missing — combined status reflects that
    assert result.status == SkillStatus.WARNING
    assert result.data["country_challenge"]["would_add"] is True
    assert result.data["validator_allowlist"]["would_add"] is True
    assert len(result.data["audit"]["issues"]) >= 2


@pytest.mark.asyncio
@respx.mock
async def test_apply_pushes_both_rules_in_order(skill, monkeypatch, tmp_path):
    """Composer must push challenge first, then allowlist (so allowlist can sit before challenge)."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_geo()  # US-dominant pre-check passes

    # Walk through state changes: empty → has country → has both
    respx.get(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint"
    ).mock(side_effect=[
        httpx.Response(200, json=_ruleset_response([])),
        httpx.Response(200, json=_ruleset_response([COUNTRY_RULE])),
        httpx.Response(200, json=_ruleset_response([ALLOWLIST_RULE, COUNTRY_RULE])),
    ])

    post_route = respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(side_effect=[
        httpx.Response(200, json={"success": True, "result": {"rules": [COUNTRY_RULE]}}),
        httpx.Response(200, json={"success": True, "result": {"rules": [ALLOWLIST_RULE, COUNTRY_RULE]}}),
    ])

    result = await skill.run(target="drjones.com", mode="apply", verify=False, geography_days=1)

    assert result.status == SkillStatus.SUCCESS
    assert post_route.call_count == 2
    assert result.data["country_challenge"]["action_taken"] == "added"
    assert result.data["validator_allowlist"]["action_taken"] == "added"
    assert result.data["audit"]["challenge_rule_exists"] is True
    assert result.data["audit"]["validator_allowlist_exists"] is True


@pytest.mark.asyncio
@respx.mock
async def test_apply_idempotent_when_both_present(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_geo()
    respx.get(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint"
    ).mock(return_value=httpx.Response(200, json=_ruleset_response([ALLOWLIST_RULE, COUNTRY_RULE])))

    post_route = respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(return_value=httpx.Response(200, json={"success": True, "result": {}}))

    result = await skill.run(target="drjones.com", mode="apply", verify=False, geography_days=1)

    assert result.status == SkillStatus.SUCCESS
    assert result.data["country_challenge"]["action_taken"] == "already_present"
    assert result.data["validator_allowlist"]["action_taken"] == "already_present"
    assert not post_route.called


@pytest.mark.asyncio
@respx.mock
async def test_apply_international_skips_challenge_but_still_pushes_allowlist(skill, monkeypatch, tmp_path):
    """International site: country challenge SKIPPED (would harm clients), but allowlist still added."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_geo({"US": 5000, "AE": 3000, "SA": 2000})  # 50% US — below 90% threshold

    respx.get(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint"
    ).mock(side_effect=[
        # push_validator_allowlist sees empty ruleset (country was skipped)
        httpx.Response(200, json=_ruleset_response([])),
        # check_waf sees just the allowlist
        httpx.Response(200, json=_ruleset_response([ALLOWLIST_RULE])),
    ])

    post_route = respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(return_value=httpx.Response(200, json={"success": True, "result": {"rules": [ALLOWLIST_RULE]}}))

    result = await skill.run(target="drjones.com", mode="apply", verify=False, geography_days=1)

    # Status WARNING because audit will flag missing challenge — that's real even though
    # we intentionally skipped it. The data field tells the why.
    assert result.status == SkillStatus.WARNING
    assert result.data["country_challenge"]["action_taken"] == "skipped_geography"
    assert result.data["country_challenge"]["geography"]["us_pct"] == 50.0
    assert result.data["validator_allowlist"]["action_taken"] == "added"
    assert any("challenge" in i.lower() for i in result.data["audit"]["issues"])
    assert post_route.call_count == 1  # only allowlist got pushed


@pytest.mark.asyncio
@respx.mock
async def test_invalid_mode(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)

    result = await skill.run(target="drjones.com", mode="bogus")
    assert result.status == SkillStatus.FAILURE


@pytest.mark.asyncio
@respx.mock
async def test_country_failure_aborts_composer(skill, monkeypatch, tmp_path):
    """If the country challenge push fails, composer must NOT push the allowlist."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_geo()  # geo passes; failure happens at rule push
    respx.get(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint"
    ).mock(return_value=httpx.Response(200, json=_ruleset_response([])))
    respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(return_value=httpx.Response(403, json={"success": False, "errors": [{"message": "forbidden"}]}))

    result = await skill.run(target="drjones.com", mode="apply", verify=False, geography_days=1)

    assert result.status == SkillStatus.FAILURE
    assert "country challenge" in result.message.lower()
    # Allowlist should not have been attempted
    assert "validator_allowlist" not in result.data or result.data.get("validator_allowlist") is None
