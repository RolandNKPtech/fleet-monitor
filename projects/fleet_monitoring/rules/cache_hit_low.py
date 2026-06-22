"""Rule: CF edge cache hit rate is below industry-target bands.

Cache hit rate = `sum.cachedRequests / sum.requests` from
httpRequests1dGroups, computed in cf_api.parse_analytics_response. CF's
own guidance is >90% for static-heavy sites and >70% for dynamic
(WordPress) sites; below 50% indicates a serious caching misconfig
(usually a cache rule that bypasses too much — see the
`cf_cache_rules_last_match_wins` lesson for example-cosprd).

Two bands:
  - <50%  : critical (broken — likely a bad cache rule routing everything
            to origin)
  - <70%  : warning  (under-cached — operator should investigate which
            paths aren't hitting)

Volume floor: under 10k requests in 30d the ratio is noisy (a single
cache-miss page can drag the rate). The threshold avoids spam-firing on
quiet sites that don't have enough samples for the rate to be meaningful.
"""
from __future__ import annotations
from ..models import Alert, SEVERITY_CRITICAL, SEVERITY_WARNING

RULE_ID = "cache_hit_low"
MIN_REQUESTS_30D = 10_000
WARN_PCT = 70.0
CRIT_PCT = 50.0


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    an = (site.get("cf") or {}).get("analytics") or {}
    requests = an.get("requests_30d") or 0
    if requests < MIN_REQUESTS_30D:
        return []
    pct = an.get("cache_hit_rate")
    if pct is None or pct >= WARN_PCT:
        return []
    severity = SEVERITY_CRITICAL if pct < CRIT_PCT else SEVERITY_WARNING
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=severity,
        summary=(f"cache hit {pct:.1f}% over 30d ({requests:,} requests) — "
                 f"{'broken cache' if pct < CRIT_PCT else 'under-cached'}, "
                 f"check cache rules"),
        detail={
            "cache_hit_rate": pct,
            "requests_30d": requests,
        },
    )]
