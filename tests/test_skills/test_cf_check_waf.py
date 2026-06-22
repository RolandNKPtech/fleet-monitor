import pytest
import respx
import httpx
from skills.cloudflare.check_waf import CheckWafSkill
from skills.cloudflare.client import _reset_client
from skills.base import SkillStatus
from tests.conftest import load_fixture


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill():
    return CheckWafSkill()


def _mock_zone(domain="drjones.com", zone_id="zone_001"):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": domain}).mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": [{"id": zone_id, "name": domain}],
            "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1}
        })
    )


@pytest.mark.asyncio
@respx.mock
async def test_standard_rule_exists(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint").mock(
        return_value=httpx.Response(200, json=load_fixture("cf_waf_rules_full"))
    )
    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.SUCCESS
    assert result.data["challenge_rule_exists"] is True
    assert result.data["expression_matches"] is True


@pytest.mark.asyncio
@respx.mock
async def test_no_custom_rules(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint").mock(
        return_value=httpx.Response(200, json=load_fixture("cf_waf_rules_empty"))
    )
    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.WARNING
    assert result.data["challenge_rule_exists"] is False


@pytest.mark.asyncio
@respx.mock
async def test_wrong_expression(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint").mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": {"id": "rs1", "phase": "http_request_firewall_custom", "rules": [
                {"id": "r1", "expression": "(ip.src.country ne \"US\")", "action": "managed_challenge", "description": "Old rule", "enabled": True}
            ]}
        })
    )
    result = await skill.run(target="drjones.com")
    assert result.data["challenge_rule_exists"] is True
    assert result.data["expression_matches"] is False


@pytest.mark.asyncio
@respx.mock
async def test_disabled_rule(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint").mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": {"id": "rs1", "phase": "http_request_firewall_custom", "rules": [
                {"id": "r1", "expression": "(ip.src.country ne \"US\" and not cf.client.bot)", "action": "managed_challenge", "description": "Challenge", "enabled": False}
            ]}
        })
    )
    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.WARNING
    assert any("disabled" in i for i in result.data["issues"])


@pytest.mark.asyncio
@respx.mock
async def test_multiple_rules(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint").mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": {"id": "rs1", "phase": "http_request_firewall_custom", "rules": [
                {"id": "r0", "expression": "(http.user_agent contains \"Schema-Markup-Validator\")", "action": "skip", "description": "NKP: validator + crawler allowlist", "enabled": True},
                {"id": "r1", "expression": "(ip.src.country ne \"US\" and not cf.client.bot)", "action": "managed_challenge", "description": "Challenge", "enabled": True},
                {"id": "r2", "expression": "(http.request.uri.path contains \"/xmlrpc.php\")", "action": "block", "description": "Block XMLRPC", "enabled": True}
            ]}
        })
    )
    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.SUCCESS
    assert result.data["total_custom_rules"] == 3
    assert result.data["validator_allowlist_exists"] is True


@pytest.mark.asyncio
@respx.mock
async def test_missing_validator_allowlist_is_warning(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint").mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": {"id": "rs1", "phase": "http_request_firewall_custom", "rules": [
                {"id": "r1", "expression": "(ip.src.country ne \"US\" and not cf.client.bot)", "action": "managed_challenge", "description": "Challenge Non-US Traffic", "enabled": True}
            ]}
        })
    )
    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.WARNING
    assert result.data["validator_allowlist_exists"] is False
    assert any("allowlist" in i.lower() for i in result.data["issues"])


@pytest.mark.asyncio
@respx.mock
async def test_validator_allowlist_wrong_order_is_warning(skill, monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    # Allowlist at position 1 (after country challenge) — wrong order
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_request_firewall_custom/entrypoint").mock(
        return_value=httpx.Response(200, json={
            "success": True, "result": {"id": "rs1", "phase": "http_request_firewall_custom", "rules": [
                {"id": "r1", "expression": "(ip.src.country ne \"US\" and not cf.client.bot)", "action": "managed_challenge", "description": "Challenge Non-US Traffic", "enabled": True},
                {"id": "r0", "expression": "(http.user_agent contains \"Schema-Markup-Validator\")", "action": "skip", "description": "NKP: validator + crawler allowlist", "enabled": True}
            ]}
        })
    )
    result = await skill.run(target="drjones.com")
    assert result.status == SkillStatus.WARNING
    assert result.data["validator_allowlist_exists"] is True
    assert any("wrong order" in i for i in result.data["issues"])
