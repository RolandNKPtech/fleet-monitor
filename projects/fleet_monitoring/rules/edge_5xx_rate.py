"""Rule: edge 5xx rate elevated over the last 7 calendar days.

The metric is `edgeResponseStatus` from CF GraphQL — what Cloudflare returned
to the client. That is a strict SUPERSET of origin 5xx because it includes:
  - Origin 5xx that CF passed through (real backend 500/502/503/504)
  - CF gateway codes 520-526 (origin misbehaved: timeouts, SSL handshake
    failure, connection refused)
  - 530 (origin DNS resolution failed)

So this is the operator-facing "how often did this site fail to serve
clients" signal — the cleaner backend-health metric than raw origin status.

Volume floors keep the rule honest on the long tail. A 50-request site that
returned 1 error is at 2% but means nothing; a 5000-request site with the
same 2% means real users hit real failures. Two floors:
  - MIN_REQUESTS_7D: minimum 7d request count to compute a rate at all
  - MIN_5XX_EVENTS:  minimum absolute 5xx count to fire — protects against
                     noise from sites that hover near the floor

Window: trailing 7 calendar days anchored on the latest day with data in
the snapshot. See cf_api.parse_analytics_response for the exact semantics.
"""
from __future__ import annotations
from ..models import Alert, SEVERITY_CRITICAL, SEVERITY_WARNING

RULE_ID = "edge_5xx_rate"
MIN_REQUESTS_7D = 1_000        # ~143 req/day — covers most of the fleet
MIN_5XX_EVENTS = 10            # need at least this many real errors to fire
WARN_PCT = 1.0
CRIT_PCT = 3.0


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    cf_an = (site.get("cf") or {}).get("analytics") or {}
    req_7d = cf_an.get("requests_7d") or 0
    err_7d = cf_an.get("requests_5xx_7d") or 0
    if req_7d < MIN_REQUESTS_7D or err_7d < MIN_5XX_EVENTS:
        return []
    pct = cf_an.get("pct_5xx_7d")
    if pct is None or pct < WARN_PCT:
        return []
    severity = SEVERITY_CRITICAL if pct >= CRIT_PCT else SEVERITY_WARNING
    top = cf_an.get("top_status_codes_7d") or []
    top5xx = [r for r in top if 500 <= r.get("code", 0) < 600][:3]
    top_label = ", ".join(f"{r['code']}={r['requests']:,}" for r in top5xx) or "—"
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=severity,
        summary=(f"edge 5xx {pct:.2f}% over last 7d "
                 f"({err_7d:,}/{req_7d:,}) — top codes: {top_label}"),
        detail={
            "pct_5xx_7d": pct,
            "requests_5xx_7d": err_7d,
            "requests_7d": req_7d,
            "top_5xx_codes_7d": top5xx,
        },
    )]
