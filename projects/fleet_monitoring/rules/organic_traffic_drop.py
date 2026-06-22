"""Rule: GSC clicks dropped sharply week-over-week — real organic traffic loss."""
from __future__ import annotations
from ..models import Alert, SEVERITY_CRITICAL, SEVERITY_WARNING

RULE_ID = "organic_traffic_drop"
MIN_PREV_CLICKS = 50
WARN_DROP = 0.30
CRIT_DROP = 0.50


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    gsc = (site.get("analytics") or {}).get("gsc") or {}
    c7, c_prev = gsc.get("clicks_7d"), gsc.get("clicks_prev_7d")
    if c7 is None or c_prev is None or c_prev < MIN_PREV_CLICKS:
        return []
    drop = (c_prev - c7) / c_prev
    if drop < WARN_DROP:
        return []
    sev = SEVERITY_CRITICAL if drop >= CRIT_DROP else SEVERITY_WARNING
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=sev,
        summary=(f"GSC clicks {-drop*100:+.0f}% week-over-week "
                 f"({int(c7):,} vs {int(c_prev):,})"),
        detail={"clicks_7d": int(c7), "clicks_prev_7d": int(c_prev),
                "drop": round(drop, 4)},
    )]
