"""Check the geographic distribution of a CF zone's traffic.

Used as a pre-check before applying the country challenge rule (which blocks
non-US traffic). If a site has significant non-US traffic — international
medical tourism, cross-border consultations, etc. — the challenge rule would
lock out paying customers.

Returns the country breakdown + a US-dominance verdict.
"""
from datetime import datetime, timedelta, timezone

from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.check_traffic_geography")

DEFAULT_DAYS = 7
DEFAULT_MIN_US_PCT = 90.0
TOP_N_COUNTRIES = 10

# httpRequestsAdaptiveGroups is limited to 24h windows; we loop over days.
COUNTRY_QUERY = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      httpRequestsAdaptiveGroups(
        filter: {datetime_geq: "%s", datetime_leq: "%s"}
        limit: 200
        orderBy: [count_DESC]
      ) {
        count
        dimensions { clientCountryName }
      }
    }
  }
}
"""


class CheckTrafficGeographySkill(BaseSkill):
    name = "cloudflare.check_traffic_geography"
    description = (
        "Check geographic distribution of zone traffic. Returns top countries + "
        "US-dominance verdict. Use as pre-check before applying country challenge rule."
    )
    required_inputs = ["target"]
    optional_inputs = ["days", "min_us_pct"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]
        days = int(kwargs.get("days") or DEFAULT_DAYS)
        min_us_pct = float(kwargs.get("min_us_pct") or DEFAULT_MIN_US_PCT)

        try:
            client = get_cf_client()
            zone_id = await client.get_zone_id(target)

            now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now - timedelta(days=1)
            start_date = end_date - timedelta(days=days - 1)

            # Aggregate country counts across the window. We can use one window query
            # per day (24h limit on adaptive groups), then sum.
            country_totals: dict[str, int] = {}
            for i in range(days):
                day_start = start_date + timedelta(days=i)
                day_end = day_start + timedelta(days=1) - timedelta(seconds=1)

                resp = await client.graphql(
                    COUNTRY_QUERY % (
                        zone_id,
                        day_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        day_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    )
                )
                groups = (
                    resp.get("data", {})
                    .get("viewer", {})
                    .get("zones", [{}])[0]
                    .get("httpRequestsAdaptiveGroups", [])
                )
                for g in groups:
                    country = g["dimensions"]["clientCountryName"] or "UNKNOWN"
                    country_totals[country] = country_totals.get(country, 0) + g["count"]

        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"{target}: traffic geography check failed — {e}",
                errors=[str(e)],
            )

        total = sum(country_totals.values())
        us_count = country_totals.get("US", 0) + country_totals.get("United States", 0)

        if total == 0:
            return SkillResult(
                status=SkillStatus.WARNING,
                data={
                    "domain": target,
                    "days": days,
                    "min_us_pct": min_us_pct,
                    "total_requests": 0,
                    "us_count": 0,
                    "us_pct": None,
                    "is_us_dominant": False,
                    "top_countries": [],
                },
                message=f"{target}: no traffic data in last {days}d — cannot verify US-dominance",
            )

        us_pct = round((us_count / total) * 100, 2)
        is_us_dominant = us_pct >= min_us_pct

        ranked = sorted(country_totals.items(), key=lambda x: x[1], reverse=True)[:TOP_N_COUNTRIES]
        top_countries = [
            {"country": c, "count": n, "pct": round((n / total) * 100, 2)}
            for c, n in ranked
        ]

        status = SkillStatus.SUCCESS if is_us_dominant else SkillStatus.WARNING
        verdict = "US-dominant" if is_us_dominant else "international (challenge rule would harm)"
        return SkillResult(
            status=status,
            data={
                "domain": target,
                "days": days,
                "min_us_pct": min_us_pct,
                "total_requests": total,
                "us_count": us_count,
                "us_pct": us_pct,
                "is_us_dominant": is_us_dominant,
                "top_countries": top_countries,
            },
            message=f"{target}: {us_pct}% US over {days}d ({total:,} req) — {verdict}",
        )
