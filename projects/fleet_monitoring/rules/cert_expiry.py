"""Rule: SSL/TLS certificate approaching expiry or already expired.

Single biggest preventable outage in the fleet — a lapsed cert takes a site
dark with a browser scare-screen until renewal. CF Universal SSL renews
~30 days before expiry so most zones never trip this; the rule's value is
catching auto-renewal FAILURES (DCV issues, account changes) and custom
certs that depend on manual renewal.

Data source: cf.cert_expiry.min_days_until_expiry from
cf_api.parse_cert_packs (active packs only, earliest expiry across packs).
A None min_days means we couldn't read cert state — skip rather than alert.

Thresholds:
  - days <= 0           : critical (EXPIRED — likely already serving warnings)
  - 0 < days <= 14      : critical (urgent — auto-renewal window has passed)
  - 14 < days <= 30     : warning  (schedule renewal; CF renews around here)
"""
from __future__ import annotations
from ..models import Alert, SEVERITY_CRITICAL, SEVERITY_WARNING

RULE_ID = "cert_expiry"
WARN_DAYS = 30
CRIT_DAYS = 14


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    ce = (site.get("cf") or {}).get("cert_expiry") or {}
    days = ce.get("min_days_until_expiry")
    if days is None:
        return []
    if days > WARN_DAYS:
        return []

    expires_on = ce.get("earliest_expires_on") or "?"
    issuer = ce.get("earliest_issuer") or "unknown CA"

    if days <= 0:
        severity = SEVERITY_CRITICAL
        summary = (f"SSL cert EXPIRED {abs(days)} days ago "
                   f"(expired {expires_on}, issued by {issuer}) — "
                   f"browsers blocking traffic")
    elif days <= CRIT_DAYS:
        severity = SEVERITY_CRITICAL
        summary = (f"SSL cert expires in {days} days on {expires_on} "
                   f"(issued by {issuer}) — urgent renewal window")
    else:
        severity = SEVERITY_WARNING
        summary = (f"SSL cert expires in {days} days on {expires_on} "
                   f"(issued by {issuer}) — schedule renewal")
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=severity,
        summary=summary,
        detail={
            "days_until_expiry": days,
            "expires_on": expires_on,
            "issuer": issuer,
            "active_pack_count": ce.get("active_pack_count"),
            "pack_id": ce.get("earliest_pack_id"),
        },
    )]
