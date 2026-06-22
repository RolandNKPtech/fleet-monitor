"""Cloudflare fetch helpers — wraps skills/cloudflare/client.py.

Every fetch is failure-isolated: on any exception it returns an empty/partial
structure plus an "error" marker, so one bad zone never aborts a fleet run.
"""
from __future__ import annotations
from datetime import date as _date, datetime, timedelta, timezone

from skills.cloudflare.client import CloudflareClient
from .digest import build_digest

_ANALYTICS_QUERY = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      httpRequests1dGroups(filter: {date_geq: "%s", date_leq: "%s"}, limit: 31) {
        sum {
          requests
          cachedRequests
          threats
          responseStatusMap { edgeResponseStatus requests }
        }
        dimensions { date }
      }
    }
  }
}
"""


def _status_sums(status_map: list) -> tuple[int, int, dict]:
    """Return (5xx_total, 4xx_total, per_code_counts) from one responseStatusMap."""
    s5xx = s4xx = 0
    by_code: dict[int, int] = {}
    for entry in status_map or []:
        code = int(entry.get("edgeResponseStatus") or 0)
        cnt = int(entry.get("requests") or 0)
        if code <= 0:
            continue
        by_code[code] = by_code.get(code, 0) + cnt
        if 500 <= code < 600:
            s5xx += cnt
        elif 400 <= code < 500:
            s4xx += cnt
    return s5xx, s4xx, by_code


def parse_analytics_response(raw: dict) -> dict:
    """Reduce a GraphQL httpRequests1dGroups response to fleet metrics.

    The 7-day window is anchored on the LATEST date present in the data —
    not the last 7 list entries. CF GraphQL only returns days that had
    traffic; for a low-traffic site, the last 7 *entries* could span weeks
    of calendar time. We compute `cutoff = max_date - 6 days` and include
    every group whose date is >= cutoff. That keeps "7d" honest as a
    calendar window even when the site had quiet days.

    Edge case: if the cron breaks and max_date is several days stale, the
    7d window slides back with it. That's correct — the alert reflects the
    data we actually have, not pretend-7-days extending into empty space.
    """
    groups = (raw.get("data", {}).get("viewer", {}).get("zones") or [{}])[0] \
        .get("httpRequests1dGroups", []) or []
    groups = sorted(groups, key=lambda g: (g.get("dimensions") or {}).get("date", ""))

    requests = sum(g["sum"]["requests"] for g in groups)
    cached = sum(g["sum"]["cachedRequests"] for g in groups)
    threats = sum(g["sum"]["threats"] for g in groups)

    s5xx_30d = 0
    for g in groups:
        s5xx, _, _ = _status_sums((g["sum"] or {}).get("responseStatusMap") or [])
        s5xx_30d += s5xx

    # Date-anchored 7d window: keep entries within 6 days back from latest.
    last7: list[dict] = []
    if groups:
        max_date_str = (groups[-1].get("dimensions") or {}).get("date", "")
        try:
            max_date = _date.fromisoformat(max_date_str)
            cutoff = max_date - timedelta(days=6)
            for g in groups:
                d = (g.get("dimensions") or {}).get("date", "")
                try:
                    if _date.fromisoformat(d) >= cutoff:
                        last7.append(g)
                except ValueError:
                    continue
        except ValueError:
            last7 = groups[-7:]   # malformed dates → degrade to list slice

    req_7d = sum(g["sum"]["requests"] for g in last7)
    s5xx_7d = 0
    by_code_7d: dict[int, int] = {}
    for g in last7:
        s5xx, _, by_code = _status_sums((g["sum"] or {}).get("responseStatusMap") or [])
        s5xx_7d += s5xx
        for c, n in by_code.items():
            by_code_7d[c] = by_code_7d.get(c, 0) + n

    top_codes_7d = sorted(by_code_7d.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return {
        "requests_30d": requests,
        "threats": threats,
        "cache_hit_rate": round(cached / requests * 100, 1) if requests else 0.0,
        "requests_5xx_30d": s5xx_30d,
        "pct_5xx_30d": round(s5xx_30d / requests * 100, 2) if requests else 0.0,
        "requests_7d": req_7d,
        "requests_5xx_7d": s5xx_7d,
        "pct_5xx_7d": round(s5xx_7d / req_7d * 100, 2) if req_7d else 0.0,
        "top_status_codes_7d": [{"code": c, "requests": n} for c, n in top_codes_7d],
    }


def dns_proxy_state(records: list[dict], apex: str) -> tuple[bool | None, bool | None]:
    """Return (apex_proxied, www_proxied). None if the record is absent."""
    apex_p, www_p = None, None
    for rec in records or []:
        name = rec.get("name", "")
        if name == apex and rec.get("type") in ("A", "AAAA", "CNAME"):
            apex_p = bool(rec.get("proxied"))
        elif name == f"www.{apex}" and rec.get("type") in ("A", "AAAA", "CNAME"):
            www_p = bool(rec.get("proxied"))
    return apex_p, www_p


async def fetch_zone_config(client: CloudflareClient, zone_id: str, apex: str) -> dict:
    """Fetch a zone's full config digest. Failure-isolated — returns {error: ...} on failure."""
    try:
        settings = await client.get_zone_settings(zone_id)
    except Exception as e:
        return {"error": f"settings: {e}"}
    try:
        bm = await client.get(f"/zones/{zone_id}/bot_management")
        bot = bm.get("result", {}) or {}
    except Exception:
        bot = {}
    try:
        waf = await client.get_ruleset(zone_id, "http_request_firewall_custom")
        waf_rules = (waf or {}).get("rules", []) or []
    except Exception:
        waf_rules = []
    try:
        cache = await client.get_ruleset(zone_id, "http_request_cache_settings")
        cache_rules = (cache or {}).get("rules", []) or []
    except Exception:
        cache_rules = []
    try:
        dns = await client.get(f"/zones/{zone_id}/dns_records?per_page=100")
        apex_p, www_p = dns_proxy_state(dns.get("result", []), apex)
    except Exception:
        apex_p, www_p = None, None
    return build_digest(settings, bot, waf_rules, cache_rules, apex_p, www_p)


async def fetch_zone_analytics(client: CloudflareClient, zone_id: str) -> dict:
    """Fetch 30-day zone analytics. Failure-isolated — returns zeros on failure."""
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=29)
    try:
        raw = await client.graphql(_ANALYTICS_QUERY % (
            zone_id, start.isoformat(), end.isoformat()))
        return parse_analytics_response(raw)
    except Exception:
        return {
            "requests_30d": 0, "threats": 0, "cache_hit_rate": 0.0,
            "requests_5xx_30d": 0, "pct_5xx_30d": 0.0,
            "requests_7d": 0, "requests_5xx_7d": 0, "pct_5xx_7d": 0.0,
            "top_status_codes_7d": [], "error": True,
        }


def parse_cert_packs(packs: list, today: _date) -> dict:
    """Reduce raw certificate_packs response to a min-days-until-expiry summary.

    Only `status == "active"` packs count — pending packs aren't serving
    traffic yet. Within each active pack the earliest `expires_on` across
    its certificates is the pack's expiry. Across packs we take the
    earliest — that's the cert most at risk of taking the site down.

    Returns `{min_days_until_expiry: None}` if no active pack has a parseable
    expiry; downstream rules treat None as "no signal" (no false alert).
    """
    earliest: _date | None = None
    earliest_pack_id: str | None = None
    earliest_issuer: str | None = None
    active_count = 0
    for pack in packs or []:
        if pack.get("status") != "active":
            continue
        active_count += 1
        for cert in pack.get("certificates", []) or []:
            exp_str = cert.get("expires_on") or cert.get("expires_at") or ""
            # CF returns ISO-8601 with timezone: "2026-08-15T23:59:59Z"
            try:
                exp = _date.fromisoformat(exp_str[:10])
            except (ValueError, TypeError):
                continue
            if earliest is None or exp < earliest:
                earliest = exp
                earliest_pack_id = pack.get("id")
                earliest_issuer = (cert.get("issuer")
                                   or pack.get("certificate_authority"))
    if earliest is None:
        return {"min_days_until_expiry": None,
                "active_pack_count": active_count}
    return {
        "min_days_until_expiry": (earliest - today).days,
        "earliest_expires_on": earliest.isoformat(),
        "active_pack_count": active_count,
        "earliest_pack_id": earliest_pack_id,
        "earliest_issuer": earliest_issuer,
    }


async def fetch_zone_cert_expiry(client: CloudflareClient, zone_id: str) -> dict:
    """Fetch SSL cert pack expiry summary for one zone. Failure-isolated.

    On any API error returns `{min_days_until_expiry: None, error: True}`
    so the cert_expiry rule simply skips the site (no false positive) rather
    than spam-firing on transient API failures.
    """
    try:
        resp = await client.get(f"/zones/{zone_id}/ssl/certificate_packs",
                                params={"status": "all"})
        packs = resp.get("result", []) or []
        today = datetime.now(timezone.utc).date()
        return parse_cert_packs(packs, today)
    except Exception:
        return {"min_days_until_expiry": None, "active_pack_count": 0,
                "error": True}
