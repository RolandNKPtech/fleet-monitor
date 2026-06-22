import json
import pytest
import respx
import httpx

from skills.cloudflare.push_validator_allowlist import (
    PushValidatorAllowlistSkill,
    RULE_DESCRIPTION,
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
    return PushValidatorAllowlistSkill()


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
                "result_info": {
                    "page": 1,
                    "per_page": 50,
                    "count": 1,
                    "total_count": 1,
                    "total_pages": 1,
                },
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
                "result": {
                    "id": ruleset_id,
                    "phase": "http_request_firewall_custom",
                    "rules": rules,
                },
            },
        )
    )


EXISTING_COUNTRY_RULE = {
    "id": "rule_country_01",
    "expression": '(ip.src.country ne "US" and not cf.client.bot)',
    "action": "managed_challenge",
    "description": "Challenge Non-US Traffic",
    "enabled": True,
}


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_reports_would_add(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_ruleset(rules=[EXISTING_COUNTRY_RULE])

    result = await skill.run(target="drjones.com", mode="dry_run")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["action_taken"] == "none"
    assert result.data["would_add"] is True
    assert result.data["existing_rules_count"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_reports_already_present(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    existing_allowlist = {
        "id": "rule_allow_01",
        "expression": '(http.user_agent contains "Schema-Markup-Validator")',
        "action": "skip",
        "description": RULE_DESCRIPTION,
        "enabled": True,
    }
    _mock_ruleset(rules=[existing_allowlist, EXISTING_COUNTRY_RULE])

    result = await skill.run(target="drjones.com", mode="dry_run")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["already_present"] is True
    assert result.data["would_add"] is False


@pytest.mark.asyncio
@respx.mock
async def test_apply_inserts_rule_before_first(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_ruleset(rules=[EXISTING_COUNTRY_RULE])

    post_route = respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "id": "rs_001",
                    "rules": [
                        {
                            "id": "rule_new_01",
                            "action": "skip",
                            "description": RULE_DESCRIPTION,
                        },
                        EXISTING_COUNTRY_RULE,
                    ],
                },
            },
        )
    )

    result = await skill.run(target="drjones.com", mode="apply", verify=False)

    assert result.status == SkillStatus.SUCCESS
    assert result.data["action_taken"] == "added"
    assert post_route.called
    body = json.loads(post_route.calls[0].request.content)
    assert body["action"] == "skip"
    assert body["description"] == RULE_DESCRIPTION
    assert body["position"] == {"before": "rule_country_01"}
    assert body["action_parameters"] == {
        "ruleset": "current",
        "phases": ["http_request_sbfm"],
    }
    # Should NOT include products — that was the over-broad scope
    assert "products" not in body["action_parameters"]


@pytest.mark.asyncio
@respx.mock
async def test_apply_is_idempotent(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    existing_allowlist = {
        "id": "rule_allow_01",
        "expression": "...",
        "action": "skip",
        "description": RULE_DESCRIPTION,
        "enabled": True,
    }
    _mock_ruleset(rules=[existing_allowlist, EXISTING_COUNTRY_RULE])

    post_route = respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(return_value=httpx.Response(200, json={"success": True, "result": {}}))

    result = await skill.run(target="drjones.com", mode="apply", verify=False)

    assert result.status == SkillStatus.SUCCESS
    assert result.data["action_taken"] == "already_present"
    assert not post_route.called


@pytest.mark.asyncio
@respx.mock
async def test_remove_deletes_rule(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    existing_allowlist = {
        "id": "rule_allow_01",
        "expression": "...",
        "action": "skip",
        "description": RULE_DESCRIPTION,
        "enabled": True,
    }
    _mock_ruleset(rules=[existing_allowlist, EXISTING_COUNTRY_RULE])

    delete_route = respx.delete(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules/rule_allow_01"
    ).mock(return_value=httpx.Response(200, json={"success": True, "result": {}}))

    result = await skill.run(target="drjones.com", mode="remove")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["action_taken"] == "removed"
    assert delete_route.called


@pytest.mark.asyncio
@respx.mock
async def test_remove_noop_when_absent(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    _mock_ruleset(rules=[EXISTING_COUNTRY_RULE])

    result = await skill.run(target="drjones.com", mode="remove")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["action_taken"] == "not_present"


@pytest.mark.asyncio
@respx.mock
async def test_no_ruleset_is_failure(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    _mock_zone()
    respx.get(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint"
    ).mock(return_value=httpx.Response(404, json={"success": False, "errors": [{"message": "not found"}]}))

    result = await skill.run(target="drjones.com", mode="apply")

    assert result.status == SkillStatus.FAILURE


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
    _mock_ruleset(rules=[EXISTING_COUNTRY_RULE])
    respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "result": {"id": "rs_001", "rules": [{"id": "rule_new_01", "description": RULE_DESCRIPTION, "action": "skip"}]}},
        )
    )

    result = await skill.run(target="drjones.com", mode="apply", verify=False)

    assert result.status == SkillStatus.SUCCESS
    log_files = list((tmp_path / "data" / "reports").glob("cf-rule-changes-*.jsonl"))
    assert len(log_files) == 1
    entries = log_files[0].read_text().strip().split("\n")
    entry = json.loads(entries[-1])
    assert entry["domain"] == "drjones.com"
    assert entry["action"] == "added"
    assert entry["rule_description"] == RULE_DESCRIPTION


@pytest.mark.asyncio
@respx.mock
async def test_changelog_disabled_when_requested(skill, monkeypatch, tmp_path):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)
    _mock_zone()
    _mock_ruleset(rules=[EXISTING_COUNTRY_RULE])
    respx.post(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/rs_001/rules"
    ).mock(return_value=httpx.Response(200, json={"success": True, "result": {"rules": []}}))

    await skill.run(target="drjones.com", mode="apply", verify=False, changelog=False)

    assert list((tmp_path / "data" / "reports").glob("cf-rule-changes-*.jsonl")) == []
