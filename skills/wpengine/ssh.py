import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from core.errors import APIError
from core.logger import get_logger

try:
    import paramiko
    from paramiko import AuthenticationException as _ParamikoAuthError
except ImportError:
    paramiko = None  # type: ignore
    _ParamikoAuthError = None  # type: ignore

log = get_logger("wpengine.ssh")


@dataclass
class SSHResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class WPESSHClient:
    """SSH client for WP Engine installs using paramiko (wrapped in asyncio.to_thread)."""

    def __init__(
        self,
        install_name: str,
        key_path: str | None = None,
    ):
        if paramiko is None:
            raise APIError("ssh", None, "paramiko not installed — run: pip install paramiko")

        self.install_name = install_name
        self.host = f"{install_name}.ssh.wpengine.net"
        self.username = install_name
        self.key_path = key_path or os.environ.get(
            "WPE_SSH_KEY_PATH",
            str(Path.home() / ".ssh" / "wpengine_ed25519")
        )

    def _connect(self) -> "paramiko.SSHClient":
        """Create and return a connected SSH client (sync)."""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(
                hostname=self.host,
                username=self.username,
                key_filename=self.key_path,
                timeout=30,
            )
            return ssh
        except _ParamikoAuthError as e:
            raise APIError("ssh", None, f"SSH auth failed for {self.host}: {e}")
        except Exception as e:
            raise APIError("ssh", None, f"SSH connection failed to {self.host}: {e}")

    def _exec_sync(self, command: str, timeout: int = 30) -> SSHResult:
        """Execute a command over SSH (sync)."""
        ssh = self._connect()
        try:
            stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
            return SSHResult(stdout=out, stderr=err, exit_code=code)
        finally:
            ssh.close()

    async def exec(self, command: str, timeout: int = 30) -> SSHResult:
        """Execute a command over SSH (async wrapper)."""
        return await asyncio.to_thread(self._exec_sync, command, timeout)

    async def wp_cli(self, command: str, timeout: int = 30) -> SSHResult:
        """Run a WP-CLI command on the install."""
        full_command = f"cd ~/sites/{self.install_name} && wp {command}"
        return await asyncio.to_thread(self._exec_sync, full_command, timeout)

    async def tail_log(self, log_path: str, lines: int = 100) -> str:
        """Tail a log file and return contents."""
        result = await self.exec(f"tail -n {lines} {log_path}")
        if result.success:
            return result.stdout
        log.warning(f"Failed to tail {log_path}: {result.stderr}")
        return ""

    async def get_site_size(self) -> str:
        """Get site directory size."""
        result = await self.exec(f"du -sh ~/sites/{self.install_name}/")
        return result.stdout.strip() if result.success else "unknown"

    async def check_wp_cli(self) -> bool:
        """Check if WP-CLI is available."""
        result = await self.exec("wp --version")
        return result.success
