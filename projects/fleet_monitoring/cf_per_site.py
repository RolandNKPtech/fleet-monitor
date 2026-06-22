"""Per-site CF GraphQL fetchers — country breakdown, requests/threats trend, top paths+UAs.

Two CF datasets, picked for what each can actually do:

- `httpRequests1dGroups` — pre-aggregated daily rollup. Supports a wide date
  window (we use 30 days) and carries `requests`, `threats`, and a nested
  `countryMap`. Drives the 30-day country breakdown + the requests/threats
  trend chart. ONE query per zone.

- `httpRequestsAdaptiveGroups` — flexible dimensions (path, user-agent) but
  Cloudflare caps each query at a 1-day window. Drives top paths + top user
  agents over a 7-day window via one query per day (7 each).

Bot vs human is NOT available: CF Bot Management (botScore / botScoreSrcName /
botManagementVerifiedBot) is a paid add-on absent on our plan tier across the
whole fleet. The user-agent table instead carries an `is_bot` flag inferred
from the UA string — a transparent heuristic shown next to the raw UA so an
operator can verify it.

All failure-isolated: a query failure on one zone returns an empty structure
plus an "error" marker, so a bad zone never aborts a fleet run.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone

COUNTRY_WINDOW_DAYS = 30
TRAFFIC_WINDOW_DAYS = 7
_TOP_N = 10
_ADAPTIVE_DAY_LIMIT = 20      # rows pulled per day before the 7-day merge

# ---------------------------------------------------------------------------
# 30-day country + requests/threats — httpRequests1dGroups (one query)
# ---------------------------------------------------------------------------

COUNTRY_THREATS_QUERY = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      httpRequests1dGroups(filter: {date_geq: "%s", date_leq: "%s"}, limit: 31) {
        dimensions { date }
        sum {
          requests
          threats
          countryMap { clientCountryName requests bytes }
        }
      }
    }
  }
}
"""


def _day_groups(raw: dict) -> list[dict]:
    """Pull the httpRequests1dGroups list out of a GraphQL response, defensively."""
    zones = (raw.get("data") or {}).get("viewer", {}).get("zones") or []
    return (zones[0] if zones else {}).get("httpRequests1dGroups", []) or []


def parse_country_breakdown(raw: dict) -> list[dict]:
    """Aggregate the per-day countryMap into a sorted [{country, requests, bytes}, ...].

    Sums each country across every day in the window, sorts by requests desc,
    keeps the top 20.
    """
    agg: dict[str, dict[str, int]] = {}
    for g in _day_groups(raw):
        for c in ((g.get("sum") or {}).get("countryMap") or []):
            name = c.get("clientCountryName") or "??"
            a = agg.setdefault(name, {"requests": 0, "bytes": 0})
            a["requests"] += int(c.get("requests") or 0)
            a["bytes"] += int(c.get("bytes") or 0)
    rows = [{"country": k, "requests": v["requests"], "bytes": v["bytes"]}
            for k, v in agg.items()]
    rows.sort(key=lambda r: r["requests"], reverse=True)
    return rows[:20]


def parse_requests_threats_daily(raw: dict) -> list[dict]:
    """Per-day [{date, requests, threats}, ...] sorted oldest-first."""
    rows = []
    for g in _day_groups(raw):
        s = g.get("sum") or {}
        rows.append({
            "date": (g.get("dimensions") or {}).get("date", ""),
            "requests": int(s.get("requests") or 0),
            "threats": int(s.get("threats") or 0),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


# ---------------------------------------------------------------------------
# 7-day top paths + top user-agents — httpRequestsAdaptiveGroups (one query/day)
# ---------------------------------------------------------------------------

_ADAPTIVE_DAY_QUERY = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      httpRequestsAdaptiveGroups(
        filter: {datetime_geq: "%s", datetime_leq: "%s"},
        limit: %d,
        orderBy: [count_DESC]
      ) {
        count
        dimensions { %s }
      }
    }
  }
}
"""


def parse_adaptive_counts(raw: dict, dimension: str) -> list[tuple[str, int]]:
    """Pull [(dimension_value, count), ...] out of a 1-day adaptive response."""
    zones = (raw.get("data") or {}).get("viewer", {}).get("zones") or []
    groups = (zones[0] if zones else {}).get("httpRequestsAdaptiveGroups", []) or []
    out = []
    for g in groups:
        val = (g.get("dimensions") or {}).get(dimension)
        if val is None:
            continue
        out.append((val, int(g.get("count") or 0)))
    return out


# Substrings that mark a user-agent as an automated client. Lower-cased match.
# Kept deliberately broad — the operator sees the raw UA next to the flag.
_BOT_UA_PATTERNS = (
    "bot", "crawl", "spider", "scrap", "claude", "gptbot", "chatgpt",
    "perplexity", "ccbot", "bytespider", "ahrefs", "semrush", "dataforseo",
    "mj12", "dotbot", "facebookexternalhit", "slurp", "bingbot", "googlebot",
    "google-extended", "applebot", "petalbot", "amazonbot", "yandex",
    "headless", "python-requests", "python-httpx", "curl/", "wget", "go-http",
    "java/", "okhttp", "axios", "node-fetch", "monitor", "uptime", "pingdom",
)


def ua_looks_like_bot(ua: str) -> bool:
    """Heuristic: does this user-agent string look like an automated client?

    Inferred from the UA string only — NOT a Cloudflare bot-score (our plan
    tier has no Bot Management). The render layer labels it as inferred.
    """
    u = (ua or "").lower()
    return any(p in u for p in _BOT_UA_PATTERNS)


def _top_n_with_pct(agg: dict[str, int], total: int, value_key: str) -> list[dict]:
    """Turn {key: count} into a sorted top-N list with pct_of_total."""
    rows = []
    for k, cnt in agg.items():
        rows.append({
            value_key: k,
            "requests": cnt,
            "pct_of_total": round(cnt / total * 100, 1) if total > 0 else 0.0,
        })
    rows.sort(key=lambda r: r["requests"], reverse=True)
    return rows[:_TOP_N]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _window_bounds(today: "datetime.date | None" = None):
    """Return (d30_start, d30_end, [7 day-tuples]) — all CF data ends yesterday."""
    end = (today or datetime.now(timezone.utc).date()) - timedelta(days=1)
    d30_start = end - timedelta(days=COUNTRY_WINDOW_DAYS - 1)
    days_7 = []
    for i in range(TRAFFIC_WINDOW_DAYS):
        d = end - timedelta(days=i)
        days_7.append((d.isoformat() + "T00:00:00Z", d.isoformat() + "T23:59:59Z"))
    return d30_start.isoformat(), end.isoformat(), days_7


async def fetch_all_for_zone(client, zone_id: str) -> dict:
    """Run all per-site queries for one zone. Failure-isolated.

    Returns the full per_site block ready to attach to a snapshot site entry.
    If the 30-day country/threats query fails the block comes back with empty
    lists + {"error": True}; per-day path/UA failures are skipped individually.
    """
    d30_start, d30_end, days_7 = _window_bounds()
    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "country_window_days": COUNTRY_WINDOW_DAYS,
        "traffic_window_days": TRAFFIC_WINDOW_DAYS,
        "total_requests_7d": 0,
        "requests_threats_daily": [],
        "countries": [],
        "top_paths": [],
        "top_uas": [],
    }

    # 1. 30-day country + requests/threats — one query.
    try:
        cq = await client.graphql(
            COUNTRY_THREATS_QUERY % (zone_id, d30_start, d30_end))
        out["countries"] = parse_country_breakdown(cq)
        out["requests_threats_daily"] = parse_requests_threats_daily(cq)
    except Exception:
        out["error"] = True
        return out

    # 7-day request total — exact (un-sampled) sum from the daily rollup,
    # used as the honest denominator for path / UA percentages.
    out["total_requests_7d"] = sum(
        d["requests"] for d in out["requests_threats_daily"][-TRAFFIC_WINDOW_DAYS:])
    total = out["total_requests_7d"]

    # 2 + 3. Top paths + top user agents — 14 per-day adaptive queries
    # (7 days x 2 dimensions). Adaptive groups cap each query at 1 day, so
    # the 7-day picture is one query per day; fired concurrently here and
    # merged below. A single failed day is dropped, never fatal.
    async def _one_day(geq: str, leq: str, dimension: str):
        try:
            r = await client.graphql(_ADAPTIVE_DAY_QUERY % (
                zone_id, geq, leq, _ADAPTIVE_DAY_LIMIT, dimension))
            return parse_adaptive_counts(r, dimension)
        except Exception:
            return []

    jobs = ([_one_day(g, l, "clientRequestPath") for g, l in days_7]
            + [_one_day(g, l, "userAgent") for g, l in days_7])
    results = await asyncio.gather(*jobs)
    path_days, ua_days = results[:TRAFFIC_WINDOW_DAYS], results[TRAFFIC_WINDOW_DAYS:]

    path_agg: dict[str, int] = {}
    for day in path_days:
        for path, cnt in day:
            path_agg[path] = path_agg.get(path, 0) + cnt
    out["top_paths"] = _top_n_with_pct(path_agg, total, "path")

    ua_agg: dict[str, int] = {}
    for day in ua_days:
        for ua, cnt in day:
            ua_agg[ua] = ua_agg.get(ua, 0) + cnt
    out["top_uas"] = [
        {**row, "is_bot": ua_looks_like_bot(row["ua"])}
        for row in _top_n_with_pct(ua_agg, total, "ua")
    ]

    return out
