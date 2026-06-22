import json
from pathlib import Path

import httpx
import pytest
import respx

from skills.base import SkillStatus
from skills.cloudflare.audit_config import AuditConfigSkill
from skills.cloudflare.client import _reset_client
from tests.conftest import load_fixture

# --- fixtures ---

CF_SETTINGS_COMPLIANT = load_fixture("cf_settings_compliant")
CF_SETTINGS_DRIFTED = load_fixture("cf_settings_drifted")

SITES_JSON = {
    "sites": [
        {"domain": "drjones.com", "wpe_account": "acctA", "active": True},
        {"domain": "smithderm.com", "wpe_account": "acctA", "active": True},
    ]
}

CF_CONFIG_YML = """\
o2o_base: &o2o_base
  ssl: strict
  always_use_https: "on"
  security_header:
    enabled: true
    max_age: 31536000
  rocket_loader: "off"
  early_hints: "on"
  automatic_platform_optimization:
    enabled: false
  minify:
    css: "off"
    html: "off"
    js: "off"
  smart_tiered_cache: "on"
  cache_rule_required: true
"""


@pytest.fixture(autouse=True)
def reset():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Create a temp data dir with sites.json and cf-config.yml."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sites.json").write_text(json.dumps(SITES_JSON))
    (data_dir / "accounts.json").write_text('{"wpengine": {}}')
    standards_dir = data_dir / "standards"
    standards_dir.mkdir()
    (standards_dir / "cf-config.yml").write_text(CF_CONFIG_YML)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_skill(config_dir: Path) -> AuditConfigSkill:
    standards_path = config_dir / "data" / "standards" / "cf-config.yml"
    root_dir = config_dir
    return AuditConfigSkill(standards_path=standards_path, root_dir=root_dir)


def _mock_zone(domain: str, zone_id: str):
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
                    "page": 1, "per_page": 50,
                    "count": 1, "total_count": 1, "total_pages": 1,
                },
            },
        )
    )


def _mock_settings(zone_id: str, payload: dict):
    respx.get(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/settings"
    ).mock(return_value=httpx.Response(200, json=payload))


# --- tests ---

@pytest.mark.asyncio
@respx.mock
async def test_single_site_all_compliant(config_dir, monkeypatch):
    """Single site with all settings matching → SUCCESS, drift=[]."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("drjones.com", "zone_001")
    _mock_settings("zone_001", CF_SETTINGS_COMPLIANT)

    skill = make_skill(config_dir)
    result = await skill.run(target="drjones.com")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["drift"] == []
    assert result.data["compliant"] is True
    assert result.data["zones_audited"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_single_site_with_drift(config_dir, monkeypatch):
    """Single site with drifted fixture → WARNING, drift items with severity."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("drjones.com", "zone_001")
    _mock_settings("zone_001", CF_SETTINGS_DRIFTED)

    skill = make_skill(config_dir)
    result = await skill.run(target="drjones.com")

    assert result.status == SkillStatus.WARNING
    assert len(result.data["drift"]) > 0
    # Every drift item has a severity
    for item in result.data["drift"]:
        assert "severity" in item
    assert result.data["compliant"] is False


@pytest.mark.asyncio
@respx.mock
async def test_multiple_drifted_settings_all_listed(config_dir, monkeypatch):
    """Drifted fixture has ssl, security_header, rocket_loader, early_hints, apo, minify drifted."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("drjones.com", "zone_001")
    _mock_settings("zone_001", CF_SETTINGS_DRIFTED)

    skill = make_skill(config_dir)
    result = await skill.run(target="drjones.com")

    drifted_settings = {d["setting"] for d in result.data["drift"]}
    # All drifted settings from fixture should be reported
    assert "ssl" in drifted_settings
    assert "rocket_loader" in drifted_settings
    assert "early_hints" in drifted_settings


@pytest.mark.asyncio
@respx.mock
async def test_bulk_audit_all_compliant(config_dir, monkeypatch):
    """Bulk audit (2 sites) all compliant → SUCCESS."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("drjones.com", "zone_001")
    _mock_zone("smithderm.com", "zone_002")
    _mock_settings("zone_001", CF_SETTINGS_COMPLIANT)
    _mock_settings("zone_002", CF_SETTINGS_COMPLIANT)

    skill = make_skill(config_dir)
    result = await skill.run(target="acctA")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["drift"] == []
    assert result.data["zones_audited"] == 2
    assert result.data["zones_errored"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_bulk_with_one_zone_api_error(config_dir, monkeypatch):
    """Bulk with one zone API error → WARNING, partial results + errors list."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("drjones.com", "zone_001")
    _mock_zone("smithderm.com", "zone_002")
    _mock_settings("zone_001", CF_SETTINGS_COMPLIANT)
    # Simulate API error for zone_002
    respx.get(
        "https://api.cloudflare.com/client/v4/zones/zone_002/settings"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"success": False, "errors": [{"message": "Zone not accessible"}]},
        )
    )

    skill = make_skill(config_dir)
    result = await skill.run(target="acctA")

    assert result.status == SkillStatus.WARNING
    assert result.data["zones_audited"] == 1
    assert result.data["zones_errored"] == 1
    assert len(result.data["errors"]) == 1
    assert result.data["errors"][0]["domain"] == "smithderm.com"


@pytest.mark.asyncio
@respx.mock
async def test_ssl_drift_is_critical(config_dir, monkeypatch):
    """SSL drift should be classified as critical severity."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("drjones.com", "zone_001")
    _mock_settings("zone_001", CF_SETTINGS_DRIFTED)

    skill = make_skill(config_dir)
    result = await skill.run(target="drjones.com")

    ssl_drifts = [d for d in result.data["drift"] if d["setting"] == "ssl"]
    assert len(ssl_drifts) == 1
    assert ssl_drifts[0]["severity"] == "critical"
    assert ssl_drifts[0]["actual"] == "flexible"
    assert ssl_drifts[0]["expected"] == "strict"


@pytest.mark.asyncio
@respx.mock
async def test_rocket_loader_drift_is_high(config_dir, monkeypatch):
    """rocket_loader drift should be classified as high severity."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("drjones.com", "zone_001")
    _mock_settings("zone_001", CF_SETTINGS_DRIFTED)

    skill = make_skill(config_dir)
    result = await skill.run(target="drjones.com")

    rl_drifts = [d for d in result.data["drift"] if d["setting"] == "rocket_loader"]
    assert len(rl_drifts) == 1
    assert rl_drifts[0]["severity"] == "high"


@pytest.mark.asyncio
@respx.mock
async def test_unknown_setting_value_still_reported(config_dir, monkeypatch):
    """An unexpected/unknown setting value should still appear in drift."""
    monkeypatch.setenv("CF_API_TOKEN", "test")
    _mock_zone("drjones.com", "zone_001")

    # Build a settings response where ssl has an unknown value
    custom_settings = {
        "success": True,
        "result": [
            {"id": "ssl", "value": "unknown_mode", "editable": True},
            {"id": "rocket_loader", "value": "off", "editable": True},
            {"id": "always_use_https", "value": "on", "editable": True},
            {"id": "early_hints", "value": "on", "editable": True},
        ],
    }
    _mock_settings("zone_001", custom_settings)

    skill = make_skill(config_dir)
    result = await skill.run(target="drjones.com")

    assert result.status == SkillStatus.WARNING
    ssl_drifts = [d for d in result.data["drift"] if d["setting"] == "ssl"]
    assert len(ssl_drifts) == 1
    assert ssl_drifts[0]["actual"] == "unknown_mode"
