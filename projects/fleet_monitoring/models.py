"""Shared data structures, severity constants, and filesystem paths."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
ROOT = PROJECT_DIR.parents[1]                       # d:\nkp-ops

DATA_DIR = PROJECT_DIR / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
TIMESERIES_FILE = DATA_DIR / "timeseries.jsonl"
DAILY_FILE = DATA_DIR / "daily.jsonl"
ROSTER_FILE = DATA_DIR / "roster.json"
ALERTS_LATEST_FILE = DATA_DIR / "alerts-latest.json"
RUN_LOG_FILE = DATA_DIR / "run-log.jsonl"
MUTE_FILE = PROJECT_DIR / "config" / "alerts-mute.yml"
# dashboard.html lives inside data/ so a single Docker volume mount covers
# everything the pipeline generates (snapshots + timeseries + dashboard).
DASHBOARD_FILE = DATA_DIR / "dashboard.html"
SITES_DIR = DATA_DIR / "sites"
CONSOLE_FILE = DATA_DIR / "console.html"
INTERVENTIONS_FILE = PROJECT_DIR / "config" / "interventions.yml"
FLEET_DB = DATA_DIR / "fleet.db"
OVERLAY_FILE = (ROOT / "data" / "reports" / "fleet-bandwidth-audit-2026-04-30"
                / "monitoring" / "fixed-sites.yml")
CF_RULE_CHANGES_GLOB = str(ROOT / "data" / "reports" / "cf-rule-changes-*.jsonl")
ANALYTICS_LAKE_DIR = ROOT / "data" / "analytics"
ANALYTICS_OVERRIDES_FILE = PROJECT_DIR / "config" / "analytics-overrides.yml"

SCHEMA_VERSION = 1

# Freshness pill thresholds — single source of truth for both the overview
# dashboard and the console header.
FRESH_HOURS = 30        # green up to this
AGING_HOURS = 48        # amber up to this; red beyond


def freshness(captured_at: str) -> tuple[str, str]:
    """(label, css-class-suffix) for a snapshot's captured_at timestamp.

    css-class-suffix is `fresh` / `aging` / `stale` and is appended to a
    surface-specific prefix in each renderer (e.g. `pill ` + `fresh` for
    the overview, `fc-pill-` + `fresh` for the console).

    Three-state because operators care about HOW stale, not just yes/no:
      - fresh (<= 30h): cron is healthy; last cycle landed
      - aging (30-48h): cron skipped a cycle; data still mostly useful
      - stale (> 48h):  cron is broken or runs are failing; numbers may
                        deceive the operator
    """
    try:
        when = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "freshness unknown", "stale"
    age_h = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    label = f"{age_h:.0f}h ago" if age_h >= 1 else f"{int(age_h*60)}m ago"
    if age_h <= FRESH_HOURS:
        return f"fresh - {label}", "fresh"
    if age_h <= AGING_HOURS:
        return f"aging - {label}", "aging"
    return f"STALE - {label}", "stale"


SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"
SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}


@dataclass
class Alert:
    """One detected condition. Rules emit these; the lifecycle stage assigns `state`."""
    site_key: str
    rule: str                                       # rule id, e.g. "bandwidth_spike"
    severity: str                                   # SEVERITY_* constant
    summary: str                                    # human one-liner
    detail: dict = field(default_factory=dict)      # rule-specific structured data
    state: str = "new"                              # new | ongoing | resolved | muted
    mute_reason: str | None = None
    # Opt-in sub-key for rules that legitimately emit several alerts per site
    # (config_drift -> per field, plan_utilization -> per axis, collection_gap
    # -> per source). The fingerprint stays site:rule for rules that don't set
    # it, so day-to-day metric drift does NOT create new alerts.
    dedup_key: str = ""

    def fingerprint(self) -> str:
        """Stable identity for lifecycle diffing: site:rule, plus :dedup_key
        when the rule supplies one. Detail values are NOT hashed in — the
        same logical issue keeps the same fingerprint across analyze runs."""
        base = f"{self.site_key}:{self.rule}"
        return f"{base}:{self.dedup_key}" if self.dedup_key else base

    def to_dict(self) -> dict:
        return {
            "site_key": self.site_key, "rule": self.rule, "severity": self.severity,
            "summary": self.summary, "detail": self.detail, "state": self.state,
            "mute_reason": self.mute_reason, "dedup_key": self.dedup_key,
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Alert":
        return cls(site_key=d["site_key"], rule=d["rule"], severity=d["severity"],
                   summary=d["summary"], detail=d.get("detail", {}),
                   state=d.get("state", "new"), mute_reason=d.get("mute_reason"),
                   dedup_key=d.get("dedup_key", ""))
