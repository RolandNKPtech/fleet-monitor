"""Rule: GA4 conversion events collapsed week-over-week — form / GTM suspect."""
from __future__ import annotations
from ..models import Alert, SEVERITY_CRITICAL

RULE_ID = "conversion_drop"
MIN_PREV = 5
DROP_FLOOR = 0.50


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    ga4 = (site.get("analytics") or {}).get("ga4") or {}
    c7, c_prev = ga4.get("conversions_7d"), ga4.get("conversions_prev_7d")
    if c7 is None or c_prev is None or c_prev < MIN_PREV:
        return []
    drop = (c_prev - c7) / c_prev
    if drop < DROP_FLOOR:
        return []
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=SEVERITY_CRITICAL,
        summary=(f"GA4 conversions {-drop*100:+.0f}% week-over-week "
                 f"({int(c7)} vs {int(c_prev)}) — form/GTM suspect"),
        detail={"conversions_7d": int(c7), "conversions_prev_7d": int(c_prev),
                "drop": round(drop, 4)},
    )]
