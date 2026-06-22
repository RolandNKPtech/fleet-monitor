import pytest
import respx
import httpx
from unittest.mock import patch, MagicMock

from skills.wpengine.backup import BackupSkill
from skills.wpengine.client import _reset_client
from skills.base import SkillStatus

BASE = "https://api.wpengineapi.com/v1"


@pytest.fixture(autouse=True)
def reset_wpe_client():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def skill(monkeypatch):
    monkeypatch.setenv("WPE_API_USER", "test-user")
    monkeypatch.setenv("WPE_API_PASSWORD", "test-pass")
    return BackupSkill()


def _mock_installs(install_name="drjones", install_id="inst_001"):
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={
            "results": [{
                "id": install_id,
                "name": install_name,
                "environment": "production",
                "status": "active",
                "account": {"id": "acc_001"},
                "primary_domain": f"{install_name}.com",
            }],
            "count": 1,
        })
    )


def _mock_config(domain=None, install_name="drjones"):
    """Return a mock NKPConfig that resolves domain → install_name."""
    mock_cfg = MagicMock()
    if domain:
        mock_cfg.get_site.return_value = {"wpe_install": install_name, "domain": domain}
    else:
        mock_cfg.get_site.return_value = None
    return mock_cfg


# --- Test 1: List backups → SUCCESS with backup data ---

@pytest.mark.asyncio
@respx.mock
async def test_list_backups(skill):
    """List backups returns SUCCESS with backup data."""
    from tests.conftest import load_fixture
    backups_fixture = load_fixture("wpe_backups")

    _mock_installs()
    respx.get(f"{BASE}/installs/inst_001/backups").mock(
        return_value=httpx.Response(200, json=backups_fixture)
    )

    with patch("skills.wpengine.backup.NKPConfig") as MockConfig:
        MockConfig.return_value = _mock_config()

        result = await skill.run(target="drjones")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["install"] == "drjones"
    assert result.data["count"] == 2
    backups = result.data["backups"]
    assert any(b["id"] == "bak_001" for b in backups)
    assert any(b["id"] == "bak_002" for b in backups)
    assert "2 backup(s)" in result.message


# --- Test 2: Create backup → SUCCESS with confirmation ---

@pytest.mark.asyncio
@respx.mock
async def test_create_backup(skill):
    """Create backup returns SUCCESS with confirmation."""
    created_backup = {
        "id": "bak_003",
        "status": "queued",
        "description": "NKP Ops backup",
        "created_at": "2026-03-30T10:00:00Z",
    }

    _mock_installs()
    respx.post(f"{BASE}/installs/inst_001/backups").mock(
        return_value=httpx.Response(200, json=created_backup)
    )

    with patch("skills.wpengine.backup.NKPConfig") as MockConfig:
        MockConfig.return_value = _mock_config()

        result = await skill.run(target="drjones", action="create")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["install"] == "drjones"
    assert result.data["backup"]["id"] == "bak_003"
    assert result.data["backup"]["description"] == "NKP Ops backup"
    assert "backup created" in result.message.lower()


# --- Test 3: Install not found → FAILURE ---

@pytest.mark.asyncio
@respx.mock
async def test_install_not_found(skill):
    """Install not found returns FAILURE."""
    respx.get(f"{BASE}/installs").mock(
        return_value=httpx.Response(200, json={"results": [], "count": 0})
    )

    with patch("skills.wpengine.backup.NKPConfig") as MockConfig:
        MockConfig.return_value = _mock_config()

        result = await skill.run(target="nonexistent")

    assert result.status == SkillStatus.FAILURE
    assert "nonexistent" in result.message
    assert result.errors


# --- Test 4: API error → FAILURE ---

@pytest.mark.asyncio
@respx.mock
async def test_api_error(skill):
    """API error on backup list returns FAILURE."""
    _mock_installs()
    respx.get(f"{BASE}/installs/inst_001/backups").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )

    with patch("skills.wpengine.backup.NKPConfig") as MockConfig:
        MockConfig.return_value = _mock_config()

        result = await skill.run(target="drjones")

    assert result.status == SkillStatus.FAILURE
    assert result.errors
    assert "drjones" in result.message
