"""
Tests for wordpress.security_scan skill.

Mocks paramiko via unittest.mock.patch("skills.wpengine.ssh.paramiko").
Each test drives different SSH exec_command responses per command.
"""
import pytest
from unittest.mock import MagicMock, patch

from skills.wordpress.security_scan import SecurityScanSkill
from skills.base import SkillStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exec_result(stdout: bytes, stderr: bytes = b"", exit_code: int = 0):
    """Return a (stdin, stdout, stderr) triple that paramiko.exec_command returns."""
    stdin = MagicMock()
    out = MagicMock()
    err = MagicMock()
    out.read.return_value = stdout
    err.read.return_value = stderr
    out.channel.recv_exit_status.return_value = exit_code
    return stdin, out, err


def _build_command_router(responses: dict):
    """
    Return a side_effect function for exec_command that selects a response
    based on a substring match against the command string.

    ``responses`` maps a substring key → (stdout, stderr, exit_code).
    The first matching key wins; a ``"default"`` key is used as fallback.
    """
    def _router(command, **kwargs):
        for key, (stdout, stderr, code) in responses.items():
            if key != "default" and key in command:
                return _make_exec_result(stdout, stderr, code)
        if "default" in responses:
            stdout, stderr, code = responses["default"]
            return _make_exec_result(stdout, stderr, code)
        return _make_exec_result(b"", b"", 0)

    return _router


# ---------------------------------------------------------------------------
# Shared fixture: patches paramiko at the SSH module level
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_paramiko():
    """Patch paramiko inside skills.wpengine.ssh and wire up a basic SSHClient."""
    with patch("skills.wpengine.ssh.paramiko") as mock_pk:
        ssh_instance = MagicMock()
        mock_pk.SSHClient.return_value = ssh_instance
        mock_pk.AutoAddPolicy.return_value = MagicMock()
        ssh_instance.connect.return_value = None
        yield mock_pk, ssh_instance


@pytest.fixture
def skill():
    return SecurityScanSkill()


# ---------------------------------------------------------------------------
# Test 1: All checks pass → SUCCESS, 4/4
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_checks_pass(skill, mock_paramiko):
    """All 4 checks pass → SUCCESS with 4/4 checks passed."""
    _, ssh_instance = mock_paramiko

    ssh_instance.exec_command.side_effect = _build_command_router({
        "core verify-checksums": (b"Success: WordPress installation verifies against checksums.", b"", 0),
        "WP_DEBUG":              (b"disabled", b"", 0),
        "stat -c":               (b"440", b"", 0),
        "user list":             (b"1", b"", 0),
    })

    result = await skill.run(target="mysite")

    assert result.status == SkillStatus.SUCCESS
    assert result.data["passed_count"] == 4
    assert result.data["total_checks"] == 4
    assert "4/4" in result.message
    checks_by_name = {c["name"]: c for c in result.data["checks"]}
    assert checks_by_name["core_checksums"]["passed"] is True
    assert checks_by_name["debug_mode"]["passed"] is True
    assert checks_by_name["file_permissions"]["passed"] is True
    assert checks_by_name["admin_count"]["passed"] is True


# ---------------------------------------------------------------------------
# Test 2: Modified core files (verify-checksums exit_code=1) → WARNING
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_modified_core_files(skill, mock_paramiko):
    """verify-checksums returns exit_code 1 → WARNING, core_checksums fails."""
    _, ssh_instance = mock_paramiko

    ssh_instance.exec_command.side_effect = _build_command_router({
        "core verify-checksums": (b"Warning: File should not exist: wp-admin/evil.php", b"", 1),
        "WP_DEBUG":              (b"disabled", b"", 0),
        "stat -c":               (b"440", b"", 0),
        "user list":             (b"1", b"", 0),
    })

    result = await skill.run(target="mysite")

    assert result.status == SkillStatus.WARNING
    assert result.data["passed_count"] == 3
    assert result.data["total_checks"] == 4
    checks_by_name = {c["name"]: c for c in result.data["checks"]}
    assert checks_by_name["core_checksums"]["passed"] is False
    assert "core_checksums" in result.message


# ---------------------------------------------------------------------------
# Test 3: Debug mode enabled → WARNING
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_debug_mode_enabled(skill, mock_paramiko):
    """WP_DEBUG is enabled → WARNING, debug_mode check fails."""
    _, ssh_instance = mock_paramiko

    ssh_instance.exec_command.side_effect = _build_command_router({
        "core verify-checksums": (b"", b"", 0),
        "WP_DEBUG":              (b"enabled", b"", 0),
        "stat -c":               (b"440", b"", 0),
        "user list":             (b"2", b"", 0),
    })

    result = await skill.run(target="mysite")

    assert result.status == SkillStatus.WARNING
    checks_by_name = {c["name"]: c for c in result.data["checks"]}
    assert checks_by_name["debug_mode"]["passed"] is False
    assert "enabled" in checks_by_name["debug_mode"]["details"]
    assert "debug_mode" in result.message


# ---------------------------------------------------------------------------
# Test 4: Multiple admins (count > 2) → WARNING
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_admins(skill, mock_paramiko):
    """Admin count > 2 → WARNING, admin_count check fails."""
    _, ssh_instance = mock_paramiko

    ssh_instance.exec_command.side_effect = _build_command_router({
        "core verify-checksums": (b"", b"", 0),
        "WP_DEBUG":              (b"disabled", b"", 0),
        "stat -c":               (b"400", b"", 0),
        "user list":             (b"5", b"", 0),
    })

    result = await skill.run(target="mysite")

    assert result.status == SkillStatus.WARNING
    checks_by_name = {c["name"]: c for c in result.data["checks"]}
    assert checks_by_name["admin_count"]["passed"] is False
    assert "5" in checks_by_name["admin_count"]["details"]
    assert "admin_count" in result.message


# ---------------------------------------------------------------------------
# Test 5: SSH connection fails → FAILURE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ssh_connection_fails(skill, mock_paramiko):
    """SSH connect raises exception → FAILURE result."""
    _, ssh_instance = mock_paramiko

    ssh_instance.connect.side_effect = Exception("Connection refused")

    result = await skill.run(target="mysite")

    assert result.status == SkillStatus.FAILURE
    assert "SSH connection failed" in result.message or "mysite" in result.message
    assert len(result.errors) > 0
