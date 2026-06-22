import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import dataclass
from skills.wpengine.ssh import WPESSHClient, SSHResult
from core.errors import APIError


@pytest.fixture
def mock_paramiko():
    """Mock paramiko.SSHClient."""
    with patch("skills.wpengine.ssh.paramiko") as mock:
        ssh_instance = MagicMock()
        mock.SSHClient.return_value = ssh_instance
        mock.AutoAddPolicy.return_value = MagicMock()

        # Default: successful connection
        ssh_instance.connect.return_value = None

        # Default: successful command
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.read.return_value = b"command output"
        stderr.read.return_value = b""
        stdout.channel.recv_exit_status.return_value = 0
        ssh_instance.exec_command.return_value = (stdin, stdout, stderr)

        yield ssh_instance


def test_ssh_result_success():
    r = SSHResult(stdout="output", stderr="", exit_code=0)
    assert r.success is True


def test_ssh_result_failure():
    r = SSHResult(stdout="", stderr="error", exit_code=1)
    assert r.success is False


@pytest.mark.asyncio
async def test_exec_success(mock_paramiko):
    client = WPESSHClient(install_name="drjones")
    result = await client.exec("ls -la")
    assert result.success is True
    assert result.stdout == "command output"
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_exec_command_failure(mock_paramiko):
    stdout = MagicMock()
    stderr = MagicMock()
    stdout.read.return_value = b""
    stderr.read.return_value = b"No such file"
    stdout.channel.recv_exit_status.return_value = 1
    mock_paramiko.exec_command.return_value = (MagicMock(), stdout, stderr)

    client = WPESSHClient(install_name="drjones")
    result = await client.exec("cat /nonexistent")
    assert result.success is False
    assert result.exit_code == 1
    assert "No such file" in result.stderr


@pytest.mark.asyncio
async def test_wp_cli(mock_paramiko):
    client = WPESSHClient(install_name="drjones")
    result = await client.wp_cli("core version")
    # Should have called exec_command with "wp core version"
    call_args = mock_paramiko.exec_command.call_args[0][0]
    assert "wp core version" in call_args


@pytest.mark.asyncio
async def test_tail_log(mock_paramiko):
    log_content = b"[error] line1\n[error] line2\n[error] line3\n"
    stdout = MagicMock()
    stdout.read.return_value = log_content
    stdout.channel.recv_exit_status.return_value = 0
    mock_paramiko.exec_command.return_value = (MagicMock(), stdout, MagicMock(read=MagicMock(return_value=b"")))

    client = WPESSHClient(install_name="drjones")
    result = await client.tail_log("/var/log/php/error.log", lines=50)
    assert "line1" in result


@pytest.mark.asyncio
async def test_connection_refused(mock_paramiko):
    import paramiko
    mock_paramiko.connect.side_effect = Exception("Connection refused")

    client = WPESSHClient(install_name="drjones")
    with pytest.raises(APIError, match="ssh"):
        await client.exec("ls")


@pytest.mark.asyncio
async def test_auth_failure(mock_paramiko):
    import paramiko
    mock_paramiko.connect.side_effect = paramiko.AuthenticationException("Auth failed")

    client = WPESSHClient(install_name="drjones")
    with pytest.raises(APIError, match="ssh"):
        await client.exec("ls")


@pytest.mark.asyncio
async def test_check_wp_cli(mock_paramiko):
    client = WPESSHClient(install_name="drjones")
    result = await client.check_wp_cli()
    assert result is True


@pytest.mark.asyncio
async def test_check_wp_cli_not_found(mock_paramiko):
    stdout = MagicMock()
    stderr = MagicMock()
    stdout.read.return_value = b""
    stderr.read.return_value = b"command not found"
    stdout.channel.recv_exit_status.return_value = 127
    mock_paramiko.exec_command.return_value = (MagicMock(), stdout, stderr)

    client = WPESSHClient(install_name="drjones")
    result = await client.check_wp_cli()
    assert result is False
