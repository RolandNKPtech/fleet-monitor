"""Rule: billable visits a tiny share of total while bandwidth is meaningful."""
from __future__ import annotations
from ..models import Alert, SEVERITY_WARNING

RULE_ID = "bot_ratio"
MAX_BILLABLE_SHARE = 0.05   # billable / total below this is suspicious
MIN_BANDWIDTH_GB = 20.0
MIN_TOTAL_VISITS = 1000


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    wpe = site.get("wpe") or {}
    billable = wpe.get("billable_visits_30d") or 0
    total = wpe.get("total_visits_30d") or 0
    bw = wpe.get("bandwidth_gb_30d") or 0
    if total < MIN_TOTAL_VISITS or bw < MIN_BANDWIDTH_GB:
        return []
    share = billable / total if total else 1.0
    if share >= MAX_BILLABLE_SHARE:
        return []
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=SEVERITY_WARNING,
        summary=f"only {share*100:.1f}% of {total:,} visits billable — bot traffic eating bandwidth",
        detail={"billable_visits": billable, "total_visits": total,
                "billable_share": round(share, 4), "bandwidth_gb": bw},
    )]
