"""Rule: a managed (fixed) site whose bandwidth has climbed back toward pre-fix."""
from __future__ import annotations
from ..models import Alert, SEVERITY_CRITICAL

RULE_ID = "fix_regression"
REGRESSION_RATIO = 0.85   # current >= 85% of pre-fix baseline = regression


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    overlay = site.get("overlay")
    if not overlay or not overlay.get("fixed"):
        return []
    pre_fix = overlay.get("pre_fix_bandwidth_gb_30d")
    wpe = site.get("wpe") or {}
    current = wpe.get("bandwidth_gb_30d")
    if pre_fix is None or current is None or pre_fix <= 0:
        return []
    if current < pre_fix * REGRESSION_RATIO:
        return []
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=SEVERITY_CRITICAL,
        summary=f"fixed site back at {current:.0f} GB vs pre-fix {pre_fix:.0f} GB — fix may have lapsed",
        detail={"current_gb": current, "pre_fix_gb": pre_fix},
    )]
