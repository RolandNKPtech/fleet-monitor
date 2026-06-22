"""Per-site GA4 + GSC aggregates from the local lake.

One DuckDB connection per `pull()` call. Two bulk queries (GA4, GSC) cover
the full 30-day window for the whole fleet; results are aggregated per
apex into the shape the snapshot embeds.
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path

import duckdb


GA4_FIELDS = ("sessions_30d", "conversions_30d", "engagement_rate",
              "sessions_7d", "sessions_prev_7d",
              "conversions_7d", "conversions_prev_7d")
GSC_FIELDS = ("clicks_30d", "impressions_30d",
              "clicks_7d", "clicks_prev_7d")


def _ga4_aggregate(con, lake_path: Path, property_ids: list[str],
                   today: date) -> dict[str, dict]:
    """Return {property_id: {sessions_30d, ...}}."""
    if not property_ids:
        return {}
    glob = str(Path(lake_path) / "ga4" / "property_metrics" / "**" / "*.parquet")
    start_30 = today - timedelta(days=30)
    start_7 = today - timedelta(days=7)
    start_14 = today - timedelta(days=14)
    quoted = ",".join(f"'{p}'" for p in property_ids)
    sql = f"""
        SELECT property_id,
          SUM(sessions)                                            AS sessions_30d,
          SUM(conversions)                                         AS conversions_30d,
          SUM(sessions * engagement_rate) / NULLIF(SUM(sessions),0) AS engagement_rate,
          SUM(CASE WHEN date >= DATE '{start_7}' AND date < DATE '{today}'  THEN sessions    ELSE 0 END) AS sessions_7d,
          SUM(CASE WHEN date >= DATE '{start_14}' AND date < DATE '{start_7}' THEN sessions  ELSE 0 END) AS sessions_prev_7d,
          SUM(CASE WHEN date >= DATE '{start_7}' AND date < DATE '{today}'  THEN conversions ELSE 0 END) AS conversions_7d,
          SUM(CASE WHEN date >= DATE '{start_14}' AND date < DATE '{start_7}' THEN conversions ELSE 0 END) AS conversions_prev_7d
        FROM read_parquet('{glob}')
        WHERE date >= DATE '{start_30}' AND date <= DATE '{today}'
          AND property_id IN ({quoted})
        GROUP BY property_id
    """
    out: dict[str, dict] = {}
    try:
        rows = con.execute(sql).fetchall()
    except duckdb.Error:
        return {}            # missing files, schema mismatch — degrade to no coverage
    cols = [d[0] for d in con.description]
    for r in rows:
        d = dict(zip(cols, r))
        pid = d.pop("property_id")
        # DuckDB SUM on integer columns returns Decimal; cast to int.
        for k in ("sessions_30d", "conversions_30d", "sessions_7d",
                  "sessions_prev_7d", "conversions_7d", "conversions_prev_7d"):
            d[k] = int(d[k] or 0)
        d["engagement_rate"] = (float(d["engagement_rate"])
                                if d["engagement_rate"] is not None else None)
        out[pid] = d
    return out


def _gsc_aggregate(con, lake_path: Path, hosts: list[str],
                   today: date) -> dict[str, dict]:
    if not hosts:
        return {}
    glob = str(Path(lake_path) / "gsc" / "search_analytics" / "**" / "*.parquet")
    start_30 = today - timedelta(days=30)
    start_7 = today - timedelta(days=7)
    start_14 = today - timedelta(days=14)
    quoted = ",".join(f"'{h}'" for h in hosts)
    sql = f"""
        SELECT host,
          SUM(clicks)      AS clicks_30d,
          SUM(impressions) AS impressions_30d,
          SUM(CASE WHEN date >= DATE '{start_7}'  AND date < DATE '{today}'   THEN clicks ELSE 0 END) AS clicks_7d,
          SUM(CASE WHEN date >= DATE '{start_14}' AND date < DATE '{start_7}' THEN clicks ELSE 0 END) AS clicks_prev_7d
        FROM read_parquet('{glob}')
        WHERE date >= DATE '{start_30}' AND date <= DATE '{today}'
          AND host IN ({quoted})
        GROUP BY host
    """
    out: dict[str, dict] = {}
    try:
        rows = con.execute(sql).fetchall()
    except duckdb.Error:
        return {}            # missing files, schema mismatch — degrade to no coverage
    cols = [d[0] for d in con.description]
    for r in rows:
        d = dict(zip(cols, r))
        h = d.pop("host")
        for k in ("clicks_30d", "impressions_30d", "clicks_7d", "clicks_prev_7d"):
            d[k] = int(d[k] or 0)
        out[h] = d
    return out


def pull(mapping: dict[str, dict], today: date,
         lake_path: Path) -> dict[str, dict]:
    """Return {apex: {ga4: dict|None, gsc: dict|None}} for every apex in
    `mapping`. None means "no coverage" -- distinct from a zero-row return."""
    property_ids = sorted({m["ga4_property_id"] for m in mapping.values()
                           if m.get("ga4_property_id")})
    hosts = sorted({_gsc_host(m["gsc_site_url"])
                    for m in mapping.values() if m.get("gsc_site_url")})
    with duckdb.connect() as con:
        ga4 = _ga4_aggregate(con, lake_path, property_ids, today)
        gsc = _gsc_aggregate(con, lake_path, hosts, today)

    out: dict[str, dict] = {}
    for apex, m in mapping.items():
        pid = m.get("ga4_property_id")
        host = _gsc_host(m.get("gsc_site_url"))
        ga4_block = None
        if pid and pid in ga4:
            ga4_block = {"property_id": pid, "source": m.get("ga4_source"),
                         **{k: ga4[pid].get(k) for k in GA4_FIELDS}}
        gsc_block = None
        if host and host in gsc:
            gsc_block = {"site_url": m["gsc_site_url"],
                         "source": m.get("gsc_source"),
                         **{k: gsc[host].get(k) for k in GSC_FIELDS}}
        out[apex] = {"ga4": ga4_block, "gsc": gsc_block}
    return out


def _gsc_host(site_url: str | None) -> str | None:
    """Extract the host portion of a GSC site_url for the lake's `host` column.

    GSC uses `sc-domain:apex` for domain properties and `https://www.apex/`
    for URL properties; the lake stores the underlying host either way.
    """
    if not site_url:
        return None
    if site_url.startswith("sc-domain:"):
        return site_url[len("sc-domain:"):]
    s = site_url.replace("https://", "").replace("http://", "")
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/")
