import logging
import os

_configured = False


def _configure_root():
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger("nkp")
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))

    if not root.handlers:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        root.addHandler(console)


def get_logger(name: str, agent_run_id: str | None = None) -> logging.Logger:
    """Get a namespaced logger under 'nkp.' prefix."""
    _configure_root()
    logger = logging.getLogger(f"nkp.{name}")
    if agent_run_id:
        logger = logging.LoggerAdapter(logger, {"agent_run_id": agent_run_id})
    return logger
