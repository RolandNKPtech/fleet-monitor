"""Rule: MB-per-visit over the bot-impacted threshold from the audit playbook."""
from __future__ import annotations
from ..models import Alert, SEVERITY_WARNING, SEVERITY_CRITICAL

RULE_ID = "mb_per_visit_high"
THRESHOLD = 30.0
CRITICAL_THRESHOLD = 60.0
MIN_BANDWIDTH_GB = 10.0   # ignore trivial sites


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    wpe = site.get("wpe") or {}
    mbv = wpe.get("mb_per_visit")
    bw = wpe.get("bandwidth_gb_30d") or 0
    if mbv is None or mbv < THRESHOLD or bw < MIN_BANDWIDTH_GB:
        return []
    severity = SEVERITY_CRITICAL if mbv >= CRITICAL_THRESHOLD else SEVERITY_WARNING
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=severity,
        summary=f"{mbv:.0f} MB/visit — bot-impacted signature",
        detail={"mb_per_visit": mbv, "bandwidth_gb": bw},
    )]
