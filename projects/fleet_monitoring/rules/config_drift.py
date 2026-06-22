"""Rule: CF config changed vs the previous snapshot. One alert per change.

Attribution: a change is tagged "us" if cf-rule-changes-*.jsonl has a log entry
for that domain within ATTRIBUTION_WINDOW_H of now, else "external".
"""
from __future__ import annotations
import glob
import json
from datetime import datetime, timezone

from ..models import Alert, CF_RULE_CHANGES_GLOB
from ..digest import digest_diff

RULE_ID = "config_drift"
ATTRIBUTION_WINDOW_H = 48


def load_rule_changes() -> list[dict]:
    """Read every cf-rule-changes-*.jsonl line. Returns [] if none exist."""
    out = []
    for path in glob.glob(CF_RULE_CHANGES_GLOB):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return out


def _previous_config(history: list[dict]) -> dict | None:
    for entry in reversed(history):
        cfg = (entry.get("cf") or {}).get("config")
        if cfg and "error" not in cfg:
            return cfg
    return None


def _attribute(site_key: str, rule_changes: list[dict], now: datetime) -> str:
    for ch in rule_changes:
        if ch.get("domain", "").lower() != site_key:
            continue
        ts = ch.get("timestamp")
        if not ts:
            continue
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if abs((now - when).total_seconds()) <= ATTRIBUTION_WINDOW_H * 3600:
            return "us"
    return "external"


def evaluate(site: dict, history: list[dict], rule_changes: list[dict] | None = None) -> list[Alert]:
    current = (site.get("cf") or {}).get("config")
    if not current or "error" in current:
        return []
    previous = _previous_config(history)
    if previous is None:
        return []
    changes = digest_diff(previous, current)
    if not changes:
        return []
    if rule_changes is None:
        rule_changes = load_rule_changes()
    now = datetime.now(timezone.utc)
    attribution = _attribute(site["key"], rule_changes, now)
    alerts = []
    for ch in changes:
        alerts.append(Alert(
            site_key=site["key"], rule=RULE_ID, severity=ch["severity"],
            summary=f"{ch['field']}: {ch['old']} -> {ch['new']} ({ch['kind']}, {attribution})",
            dedup_key=ch["field"],
            detail={**ch, "attribution": attribution},
        ))
    return alerts
