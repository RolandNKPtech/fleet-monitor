"""
Tests for wpengine.plugin_audit skill.

Uses respx to mock MainWP API calls and tmp_path for required-plugins.yml.
"""
import json
import pytest
import respx
import httpx
from pathlib import Path

from skills.wpengine.plugin_audit import PluginAuditSkill
from skills.wordpress.mainwp_client import _reset_client as _reset_mainwp
from skills.base import SkillStatus

DASH = "https://mainwp.example.com"
BASE_V2 = f"{DASH}/wp-json/mainwp/v2"

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load_plugins_fixture() -> list:
    return json.loads((FIXTURES_DIR / "mainwp_plugins.json").read_text())


def _write_standards(tmp_path: Path, required: list, banned: list) -> None:
    """Write a required-plugins.yml to tmp_path/data/standards/."""
    standards_dir = tmp_path / "data" / "standards"
    standards_dir.mkdir(parents=True, exist_ok=True)
    import yaml
    content = {"required": required, "banned": banned}
    (standards_dir / "required-plugins.yml").write_text(yaml.dump(content))


# --- Default standards matching the fixture plugins ---
DEFAULT_REQUIRED = [
    {"slug": "seopress", "name": "SEOPress", "category": "seo"},
    {"slug": "contact-form-7", "name": "Contact Form 7", "category": "forms"},
    {"slug": "perfmatters", "name": "Perfmatters", "category": "performance"},
]
DEFAULT_BANNED = [
    {"slug": "wordfence", "reason": "Conflicts with Cloudflare WAF"},
]


@pytest.fixture(autouse=True)
def reset_mainwp_client():
    _reset_mainwp()
    yield
    _reset_mainwp()


@pytest.fixture
def skill(monkeypatch, tmp_path):
    monkeypatch.setenv("MAINWP_URL", DASH)
    monkeypatch.setenv("MAINWP_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)
    return PluginAuditSkill()


# ---------------------------------------------------------------------------
# Test 1: All required present, no banned → SUCCESS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_all_required_present_no_banned(skill, monkeypatch, tmp_path):
    """All required plugins are active, no banned plugins installed → SUCCESS."""
    # Write standards without wordfence in banned (so wordfence fixture won't trigger)
    _write_standards(tmp_path, DEFAULT_REQUIRED, [])

    # Plugins: seopress, contact-form-7, perfmatters active — no wordfence
    clean_plugins = [
        {"slug": "seopress", "name": "SEOPress", "version": "8.2", "active": 1},
        {"slug": "contact-form-7", "name": "Contact Form 7", "version": "6.0", "active": 1},
        {"slug": "perfmatters", "name": "Perfmatters", "version": "2.3", "active": 1},
    ]
    respx.get(f"{BASE_V2}/plugins").mock(
        return_value=httpx.Response(200, json=clean_plugins)
    )
    # Second page empty to stop pagination
    respx.get(f"{BASE_V2}/plugins").mock(
        side_effect=[
            httpx.Response(200, json=clean_plugins),
            httpx.Response(200, json=[]),
        ]
    )

    result = await skill.run(target="test-site")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["missing_required"] == []
    assert result.data["banned_installed"] == []


# ---------------------------------------------------------------------------
# Test 2: Missing required plugin → WARNING
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_missing_required_plugin(skill, tmp_path):
    """seopress removed from mock → should appear in missing_required → WARNING."""
    _write_standards(tmp_path, DEFAULT_REQUIRED, DEFAULT_BANNED)

    # seopress omitted
    plugins_no_seopress = [
        {"slug": "contact-form-7", "name": "Contact Form 7", "version": "6.0", "active": 1},
        {"slug": "perfmatters", "name": "Perfmatters", "version": "2.3", "active": 1},
    ]
    respx.get(f"{BASE_V2}/plugins").mock(
        side_effect=[
            httpx.Response(200, json=plugins_no_seopress),
            httpx.Response(200, json=[]),
        ]
    )

    result = await skill.run(target="test-site")

    assert result.status == SkillStatus.WARNING
    missing_slugs = [p["slug"] for p in result.data["missing_required"]]
    assert "seopress" in missing_slugs
    assert "seopress" in result.message.lower() or "missing" in result.message.lower()


# ---------------------------------------------------------------------------
# Test 3: Banned plugin installed → WARNING with reason
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_banned_plugin_installed(skill, tmp_path):
    """wordfence is in the fixture and in banned list → WARNING with reason."""
    _write_standards(tmp_path, DEFAULT_REQUIRED, DEFAULT_BANNED)

    plugins = _load_plugins_fixture()
    respx.get(f"{BASE_V2}/plugins").mock(
        side_effect=[
            httpx.Response(200, json=plugins),
            httpx.Response(200, json=[]),
        ]
    )

    result = await skill.run(target="test-site")

    assert result.status == SkillStatus.WARNING
    banned = result.data["banned_installed"]
    assert any(p["slug"] == "wordfence" for p in banned)
    wf = next(p for p in banned if p["slug"] == "wordfence")
    assert "Cloudflare" in wf["reason"] or "WAF" in wf["reason"]
    assert "wordfence" in result.message.lower() or "banned" in result.message.lower()


# ---------------------------------------------------------------------------
# Test 4: Deactivated plugin → reported in deactivated list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_deactivated_plugin_reported(skill, tmp_path):
    """old-plugin (active=0) from fixture should appear in deactivated list."""
    # Use standards that don't mark old-plugin as banned or required
    _write_standards(tmp_path, DEFAULT_REQUIRED, DEFAULT_BANNED)

    plugins = _load_plugins_fixture()
    respx.get(f"{BASE_V2}/plugins").mock(
        side_effect=[
            httpx.Response(200, json=plugins),
            httpx.Response(200, json=[]),
        ]
    )

    result = await skill.run(target="test-site")

    deactivated_slugs = [p["slug"] for p in result.data["deactivated"]]
    assert "old-plugin" in deactivated_slugs


# ---------------------------------------------------------------------------
# Test 5: Non-standard plugin (not in required or banned) → reported
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_non_standard_plugin_reported(skill, tmp_path):
    """contact-form-7 not in required standards → appears in non_standard."""
    # Standards without contact-form-7 in required, and not banned
    minimal_required = [
        {"slug": "seopress", "name": "SEOPress", "category": "seo"},
    ]
    _write_standards(tmp_path, minimal_required, [])

    plugins = [
        {"slug": "seopress", "name": "SEOPress", "version": "8.2", "active": 1},
        {"slug": "contact-form-7", "name": "Contact Form 7", "version": "6.0", "active": 1},
    ]
    respx.get(f"{BASE_V2}/plugins").mock(
        side_effect=[
            httpx.Response(200, json=plugins),
            httpx.Response(200, json=[]),
        ]
    )

    result = await skill.run(target="test-site")

    non_standard_slugs = [p["slug"] for p in result.data["non_standard"]]
    assert "contact-form-7" in non_standard_slugs
    # Non-standard alone doesn't cause WARNING (low severity — still SUCCESS if no missing/banned)
    # seopress is required and present, so no missing; no banned → SUCCESS
    assert result.status == SkillStatus.SUCCESS


# ---------------------------------------------------------------------------
# Test 6: MainWP not configured → FAILURE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mainwp_not_configured(monkeypatch, tmp_path):
    """When MAINWP_URL/MAINWP_API_KEY are not set → FAILURE."""
    monkeypatch.delenv("MAINWP_URL", raising=False)
    monkeypatch.delenv("MAINWP_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    skill = PluginAuditSkill()
    result = await skill.run(target="test-site")

    assert result.status == SkillStatus.FAILURE
    assert "mainwp" in result.message.lower() or "configured" in result.message.lower()
    assert len(result.errors) > 0
