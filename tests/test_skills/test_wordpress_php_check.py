"""
Tests for wordpress.php_check skill.

Mocks paramiko via unittest.mock.patch("skills.wpengine.ssh.paramiko").
"""
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

from skills.wordpress.php_check import PHPCheckSkill
from skills.wpengine.ssh import SSHResult
from skills.base import SkillStatus
from core.errors import APIError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "ssh_error_log.txt"


def _load_fixture() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def _make_ssh_result(content: str, exit_code: int = 0) -> SSHResult:
    return SSHResult(stdout=content, stderr="", exit_code=exit_code)


def _make_paramiko_mock(stdout_bytes: bytes = b"", exit_code: int = 0):
    """Return a mock paramiko module with exec_command wired up."""
    mock_paramiko = MagicMock()
    ssh_instance = MagicMock()
    mock_paramiko.SSHClient.return_value = ssh_instance
    mock_paramiko.AutoAddPolicy.return_value = MagicMock()
    ssh_instance.connect.return_value = None

    stdout_mock = MagicMock()
    stderr_mock = MagicMock()
    stdout_mock.read.return_value = stdout_bytes
    stderr_mock.read.return_value = b""
    stdout_mock.channel.recv_exit_status.return_value = exit_code
    ssh_instance.exec_command.return_value = (MagicMock(), stdout_mock, stderr_mock)

    return mock_paramiko, ssh_instance


# ---------------------------------------------------------------------------
# Test 1 – empty log → SUCCESS, all counts 0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_log_returns_success():
    """No errors in the log should yield SUCCESS with all counts at zero."""
    skill = PHPCheckSkill()
    mock_paramiko, _ = _make_paramiko_mock(stdout_bytes=b"", exit_code=0)

    with patch("skills.wpengine.ssh.paramiko", mock_paramiko):
        # Both the debug.log tail AND the wp_cli fallback return empty
        result = await skill.run(target="drjones")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["counts"]["total"] == 0
    assert result.data["counts"]["fatal"] == 0
    assert result.data["counts"]["warning"] == 0
    assert result.data["counts"]["notice"] == 0
    assert result.data["counts"]["deprecated"] == 0
    assert "no PHP errors" in result.message


# ---------------------------------------------------------------------------
# Test 2 – fatals found → WARNING with fatal count + details
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fatals_found_returns_warning():
    """Fatal errors in the log should yield WARNING status with count details."""
    skill = PHPCheckSkill()
    fatal_log = (
        "[29-Mar-2026 14:23:01 UTC] PHP Fatal error:  Uncaught Error in "
        "/nas/content/live/drjones/wp-content/plugins/contact-form-7/includes/mail.php:42\n"
        "[29-Mar-2026 14:35:12 UTC] PHP Fatal error:  Allowed memory size exhausted in "
        "/nas/content/live/drjones/wp-content/plugins/perfmatters/inc/functions.php:200\n"
    )
    mock_paramiko, _ = _make_paramiko_mock(stdout_bytes=fatal_log.encode())

    with patch("skills.wpengine.ssh.paramiko", mock_paramiko):
        result = await skill.run(target="drjones")

    assert result.status == SkillStatus.WARNING
    assert result.data["counts"]["fatal"] == 2
    assert "fatal" in result.message.lower()
    assert result.data["counts"]["total"] == 2
    # Details should be present
    assert len(result.data["top_errors"].get("fatal", [])) == 2


# ---------------------------------------------------------------------------
# Test 3 – multiple severities from fixture → all counted correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_severities_from_fixture():
    """Fixture contains fatals, warnings, notices, deprecated — all must be counted."""
    skill = PHPCheckSkill()
    fixture_content = _load_fixture()
    mock_paramiko, _ = _make_paramiko_mock(stdout_bytes=fixture_content.encode())

    with patch("skills.wpengine.ssh.paramiko", mock_paramiko):
        result = await skill.run(target="drjones")

    counts = result.data["counts"]
    # Fixture has: 2 fatals, 2 warnings, 1 notice, 1 deprecated
    assert counts["fatal"] == 2
    assert counts["warning"] == 2
    assert counts["notice"] == 1
    assert counts["deprecated"] == 1
    assert counts["total"] == 6
    assert result.status == SkillStatus.WARNING


# ---------------------------------------------------------------------------
# Test 4 – plugin identification from fixture
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_identification_from_fixture():
    """Plugin slugs must be extracted from file paths in the fixture."""
    skill = PHPCheckSkill()
    fixture_content = _load_fixture()
    mock_paramiko, _ = _make_paramiko_mock(stdout_bytes=fixture_content.encode())

    with patch("skills.wpengine.ssh.paramiko", mock_paramiko):
        result = await skill.run(target="drjones")

    plugins = result.data["plugins"]
    assert "contact-form-7" in plugins
    assert "divi-builder" in plugins
    assert "seopress" in plugins
    assert "old-plugin" in plugins
    assert "perfmatters" in plugins


# ---------------------------------------------------------------------------
# Test 5 – SSH connection fails → FAILURE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ssh_connection_failure_returns_failure():
    """When SSH raises APIError, the skill must return FAILURE."""
    skill = PHPCheckSkill()
    mock_paramiko = MagicMock()
    ssh_instance = MagicMock()
    mock_paramiko.SSHClient.return_value = ssh_instance
    mock_paramiko.AutoAddPolicy.return_value = MagicMock()
    ssh_instance.connect.side_effect = Exception("Connection refused")

    with patch("skills.wpengine.ssh.paramiko", mock_paramiko):
        result = await skill.run(target="drjones")

    assert result.status == SkillStatus.FAILURE
    assert len(result.errors) > 0
    assert "drjones" in result.message
