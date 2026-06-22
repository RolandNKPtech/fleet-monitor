import json
import os
from pathlib import Path
from dotenv import load_dotenv
from core.errors import ConfigError
from core.logger import get_logger

log = get_logger("config")


class NKPConfig:
    """Loads and provides access to NKP configuration, sites, and accounts."""

    def __init__(self, root_dir: Path | str | None = None):
        self.root_dir = Path(root_dir) if root_dir else Path.cwd()
        load_dotenv(self.root_dir / ".env", override=False)
        self._sites = self._load_json("data/sites.json")
        self._accounts = self._load_json("data/accounts.json")

    def _load_json(self, relative_path: str) -> dict:
        path = self.root_dir / relative_path
        if not path.exists():
            log.warning(f"Config file not found: {path}")
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid JSON in {path}: {e}")

    def get_active_sites(self) -> list[dict]:
        return [s for s in self._sites.get("sites", []) if s.get("active", True)]

    def get_site(self, domain: str) -> dict | None:
        for site in self._sites.get("sites", []):
            if site["domain"] == domain:
                return site
        return None

    def get_sites_by_account(self, account: str) -> list[dict]:
        return [s for s in self.get_active_sites() if s.get("wpe_account") == account]

    def get_account(self, name: str) -> dict | None:
        return self._accounts.get("wpengine", {}).get(name)

    def get_all_accounts(self) -> dict:
        return self._accounts.get("wpengine", {})

    def get_env(self, key: str, default: str | None = None) -> str | None:
        return os.environ.get(key, default)

    def require_env(self, key: str) -> str:
        val = os.environ.get(key)
        if not val:
            raise ConfigError(f"Required environment variable '{key}' is not set")
        return val
