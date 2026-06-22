"""Rule: GA4 real sessions are a tiny share of CF requests — bot-dominated.

Ground-truth version of the legacy `bot_ratio` rule (which used WPE billable
visits as a proxy). Both rules can fire on the same site; this one is
authoritative when it does.
"""
from __future__ import annotations
from ..models import Alert, SEVERITY_CRITICAL, SEVERITY_WARNING

RULE_ID = "bot_ratio_real"
MIN_REQUESTS = 1_000     # skip tiny / brand-new sites
WARN_RATIO = 0.10        # under 10% real = warning
CRIT_RATIO = 0.05        # under 5% real = critical


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    cf_an = (site.get("cf") or {}).get("analytics") or {}
    requests = cf_an.get("requests_30d") or 0
    ga4 = (site.get("analytics") or {}).get("ga4")
    sessions = (ga4 or {}).get("sessions_30d")
    if sessions is None or requests < MIN_REQUESTS:
        return []
    ratio = sessions / requests
    if ratio >= WARN_RATIO:
        return []
    sev = SEVERITY_CRITICAL if ratio < CRIT_RATIO else SEVERITY_WARNING
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=sev,
        summary=(f"only {ratio*100:.1f}% real-user sessions vs "
                 f"{requests:,} CF requests — bot-dominated"),
        detail={"real_sessions": int(sessions), "cf_requests": int(requests),
                "ratio": round(ratio, 4)},
    )]
