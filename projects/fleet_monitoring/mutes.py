"""Mute-list loading and application."""
from __future__ import annotations
from datetime import date
import yaml

from .models import Alert, MUTE_FILE


def load_mute_entries() -> list[dict]:
    """Read config/alerts-mute.yml. Returns [] if the file is missing."""
    if not MUTE_FILE.exists():
        return []
    data = yaml.safe_load(MUTE_FILE.read_text(encoding="utf-8")) or {}
    return data.get("mutes", []) or []


def _entry_active(entry: dict, today: str) -> bool:
    exp = entry.get("expires")
    if not exp:
        return True
    return str(today) <= str(exp)


def _matches(alert: Alert, entry_fp: str) -> bool:
    """An entry matches if it equals the full fingerprint OR is a 'site:rule' prefix."""
    fp = alert.fingerprint()
    if entry_fp == fp:
        return True
    return fp.startswith(entry_fp + ":") or f"{alert.site_key}:{alert.rule}" == entry_fp


def apply_mutes(alerts: list[Alert], mute_entries: list[dict],
                today: str | None = None) -> list[Alert]:
    """Set state='muted' + mute_reason on any alert matching an active mute entry."""
    today = today or date.today().isoformat()
    active = [e for e in mute_entries if _entry_active(e, today)]
    for alert in alerts:
        for entry in active:
            if _matches(alert, entry.get("fingerprint", "")):
                alert.state = "muted"
                alert.mute_reason = entry.get("reason")
                break
    return alerts
