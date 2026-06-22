"""Rule: an unmanaged site showing the bot-impacted signature — an audit candidate."""
from __future__ import annotations
from ..models import Alert, SEVERITY_INFO

RULE_ID = "new_offender"
MBV_THRESHOLD = 30.0
MIN_BANDWIDTH_GB = 30.0


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    if site.get("overlay"):                      # already managed — skip
        return []
    wpe = site.get("wpe") or {}
    mbv = wpe.get("mb_per_visit") or 0
    bw = wpe.get("bandwidth_gb_30d") or 0
    if mbv < MBV_THRESHOLD or bw < MIN_BANDWIDTH_GB:
        return []
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=SEVERITY_INFO,
        summary=f"unmanaged site, {mbv:.0f} MB/visit, {bw:.0f} GB — audit candidate",
        detail={"mb_per_visit": mbv, "bandwidth_gb": bw},
    )]
