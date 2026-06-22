import pytest
import respx
import httpx
from skills.cloudflare.security_headers import SecurityHeadersSkill
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
    return SecurityHeadersSkill()


def _mock_zone(domain="example.com", zone_id="zone_001"):
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": domain}).mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [{"id": zone_id, "name": domain}],
            "result_info": {"page": 1, "per_page": 50, "count": 1, "total_count": 1, "total_pages": 1},
        })
    )


def _mock_settings(zone_id="zone_001", fixture="cf_settings_compliant"):
    respx.get(f"https://api.cloudflare.com/client/v4/zones/{zone_id}/settings").mock(
        return_value=httpx.Response(200, json=load_fixture(fixture))
    )


def _mock_transform_ruleset(zone_id="zone_001", fixture="cf_security_headers"):
    respx.get(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/rulesets/phases/http_response_headers_transform/entrypoint"
    ).mock(
        return_value=httpx.Response(200, json=load_fixture(fixture))
    )


# ── Test 1: HSTS on + all recommended headers present → SUCCESS ────────────────

@pytest.mark.asyncio
@respx.mock
async def test_hsts_on_all_headers_present(skill, monkeypatch):
    """All five recommended headers present + HSTS enabled → SUCCESS."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    _mock_settings()
    # Provide a ruleset with all five recommended headers
    respx.get(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_response_headers_transform/entrypoint"
    ).mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": {
                "id": "rs_full",
                "phase": "http_response_headers_transform",
                "rules": [
                    {
                        "id": "r_full",
                        "expression": "true",
                        "action": "rewrite",
                        "action_parameters": {
                            "headers": {
                                "X-Frame-Options": {"operation": "set", "value": "SAMEORIGIN"},
                                "X-Content-Type-Options": {"operation": "set", "value": "nosniff"},
                                "Referrer-Policy": {"operation": "set", "value": "strict-origin-when-cross-origin"},
                                "Permissions-Policy": {"operation": "set", "value": "geolocation=()"},
                                "Content-Security-Policy": {"operation": "set", "value": "default-src 'self'"},
                            }
                        },
                    }
                ],
            },
        })
    )

    result = await skill.run(target="example.com")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["hsts_enabled"] is True
    assert result.data["missing_headers"] == []
    assert result.data["issues"] == []


# ── Test 2: HSTS disabled → WARNING ───────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_hsts_disabled(skill, monkeypatch):
    """HSTS disabled → WARNING with HSTS issue."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    # Settings with HSTS disabled
    respx.get("https://api.cloudflare.com/client/v4/zones/zone_001/settings").mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [
                {
                    "id": "security_header",
                    "value": {
                        "strict_transport_security": {
                            "enabled": False,
                            "max_age": 0,
                            "nosniff": False,
                        }
                    },
                }
            ],
        })
    )
    _mock_transform_ruleset()  # fixture has partial headers, but HSTS issue is what we care about

    result = await skill.run(target="example.com")

    assert result.status == SkillStatus.WARNING
    assert result.data["hsts_enabled"] is False
    assert any("HSTS" in issue for issue in result.data["issues"])


# ── Test 3: No transform ruleset (None) → WARNING with missing headers ─────────

@pytest.mark.asyncio
@respx.mock
async def test_no_transform_ruleset(skill, monkeypatch):
    """get_ruleset returns None → WARNING listing all recommended headers as missing."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    _mock_settings()
    respx.get(
        "https://api.cloudflare.com/client/v4/zones/zone_001/rulesets/phases/http_response_headers_transform/entrypoint"
    ).mock(
        return_value=httpx.Response(404, json={"success": False, "errors": [{"code": 10000, "message": "Not Found"}]})
    )

    result = await skill.run(target="example.com")

    assert result.status == SkillStatus.WARNING
    assert any("No HTTP response header transform ruleset" in issue for issue in result.data["issues"])
    assert set(result.data["missing_headers"]) == {
        "X-Frame-Options",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
    }


# ── Test 4: Partial headers (some missing) → WARNING listing missing ───────────

@pytest.mark.asyncio
@respx.mock
async def test_partial_headers(skill, monkeypatch):
    """Transform rules only set 3 of 5 headers → WARNING listing the 2 missing ones."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone()
    _mock_settings()
    # cf_security_headers fixture has: X-Content-Type-Options, X-Frame-Options, Referrer-Policy
    _mock_transform_ruleset()

    result = await skill.run(target="example.com")

    assert result.status == SkillStatus.WARNING
    assert "Permissions-Policy" in result.data["missing_headers"]
    assert "Content-Security-Policy" in result.data["missing_headers"]
    # Headers from fixture should be present
    assert "X-Frame-Options" in result.data["present_headers"]
    assert "X-Content-Type-Options" in result.data["present_headers"]
    assert "Referrer-Policy" in result.data["present_headers"]
    assert any("Missing recommended headers" in issue for issue in result.data["issues"])


# ── Test 5: Zone not found → FAILURE ──────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_zone_not_found(skill, monkeypatch):
    """Zone lookup returns empty result → FAILURE."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    respx.get("https://api.cloudflare.com/client/v4/zones", params__contains={"name": "unknown.com"}).mock(
        return_value=httpx.Response(200, json={
            "success": True,
            "result": [],
            "result_info": {"page": 1, "per_page": 50, "count": 0, "total_count": 0, "total_pages": 0},
        })
    )

    result = await skill.run(target="unknown.com")

    assert result.status == SkillStatus.FAILURE
    assert result.errors
