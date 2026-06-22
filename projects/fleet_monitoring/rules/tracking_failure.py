"""Rule: GA4 sessions collapsed week-over-week while GSC clicks held.

Diagnostic -- typically a consent banner / GTM rule that broke GA tracking.
Could also be a coincidence; severity is warning so the operator inspects."""
from __future__ import annotations
from ..models import Alert, SEVERITY_WARNING

RULE_ID = "tracking_failure"
MIN_SESSIONS_PREV = 100   # noise floor on tiny sites
MIN_CLICKS_PREV = 50
GA4_DROP_FLOOR = 0.30     # GA4 must drop >= 30%
GSC_DROP_CEIL = 0.10      # GSC must hold within -10% (or rise)


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    a = site.get("analytics") or {}
    ga4 = a.get("ga4") or {}
    gsc = a.get("gsc") or {}
    s7, s_prev = ga4.get("sessions_7d"), ga4.get("sessions_prev_7d")
    c7, c_prev = gsc.get("clicks_7d"), gsc.get("clicks_prev_7d")
    if None in (s7, s_prev, c7, c_prev):
        return []
    if s_prev < MIN_SESSIONS_PREV or c_prev < MIN_CLICKS_PREV:
        return []
    ga4_drop = (s_prev - s7) / s_prev
    gsc_drop = (c_prev - c7) / c_prev
    if ga4_drop < GA4_DROP_FLOOR or gsc_drop > GSC_DROP_CEIL:
        return []
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=SEVERITY_WARNING,
        summary=(f"GA4 sessions {-ga4_drop*100:+.0f}%, "
                 f"GSC clicks {-gsc_drop*100:+.0f}% — tracking break suspected"),
        detail={"ga4_drop": round(ga4_drop, 4), "gsc_drop": round(gsc_drop, 4),
                "ga4_sessions_7d": int(s7), "ga4_sessions_prev_7d": int(s_prev),
                "gsc_clicks_7d": int(c7), "gsc_clicks_prev_7d": int(c_prev)},
    )]
