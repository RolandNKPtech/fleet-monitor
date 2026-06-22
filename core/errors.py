class NKPError(Exception):
    """Base exception for all NKP operations."""
    pass


class SkillNotFoundError(NKPError):
    """Raised when a skill is not found in the registry."""

    def __init__(self, name: str, available: list[str] | None = None):
        self.name = name
        self.available = available or []
        msg = f"Skill '{name}' not found."
        if self.available:
            msg += f" Available: {', '.join(self.available[:10])}"
        super().__init__(msg)


class ConfigError(NKPError):
    """Raised for configuration problems (missing env vars, bad config files)."""
    pass


class LLMError(NKPError):
    """Raised when Claude API calls fail after retries."""
    pass


class APIError(NKPError):
    """Raised when an external API call fails (Cloudflare, WP Engine, Monday, etc.)."""

    def __init__(self, service: str, status_code: int | None = None, message: str = ""):
        self.service = service
        self.status_code = status_code
        msg = f"{service} API error"
        if status_code:
            msg += f" ({status_code})"
        if message:
            msg += f": {message}"
        super().__init__(msg)


class AgentError(NKPError):
    """Raised when an agent encounters an unrecoverable problem."""
    pass


class PipelineError(NKPError):
    """Raised when a pipeline step fails and cannot continue."""
    pass
