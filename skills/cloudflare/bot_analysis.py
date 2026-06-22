from datetime import datetime, timedelta, timezone

from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.bot_analysis")

TRAFFIC_QUERY = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      httpRequests1dGroups(filter: {date_geq: "%s", date_leq: "%s"}, limit: 30) {
        sum { requests cachedRequests threats }
        dimensions { date }
      }
    }
  }
}
"""

SECURITY_QUERY = """
{
  viewer {
    zones(filter: {zoneTag: "%s"}) {
      firewallEventsAdaptiveGroups(filter: {datetime_geq: "%s", datetime_leq: "%s"}, limit: 100) {
        count
        dimensions { action clientCountryName }
      }
    }
  }
}
"""


class BotAnalysisSkill(BaseSkill):
    name = "cloudflare.bot_analysis"
    description = "Analyse bot/threat traffic via Cloudflare GraphQL — stitches 24-hr security event windows across N days"
    required_inputs = ["target"]
    optional_inputs = ["days"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]
        days = int(kwargs.get("days") or 7)

        try:
            client = get_cf_client()
            zone_id = await client.get_zone_id(target)

            now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now - timedelta(days=1)          # yesterday (most recent complete day)
            start_date = end_date - timedelta(days=days - 1)

            # --- Traffic totals (single query, no 24-hr window restriction) ---
            traffic_data = await client.graphql(
                TRAFFIC_QUERY % (
                    zone_id,
                    start_date.strftime("%Y-%m-%d"),
                    end_date.strftime("%Y-%m-%d"),
                )
            )

            day_groups = (
                traffic_data.get("data", {})
                .get("viewer", {})
                .get("zones", [{}])[0]
                .get("httpRequests1dGroups", [])
            )
            total_requests = sum(g["sum"]["requests"] for g in day_groups)
            total_cached = sum(g["sum"]["cachedRequests"] for g in day_groups)
            total_threats = sum(g["sum"]["threats"] for g in day_groups)

            # --- Security events (one query per day — 24-hr window limit) ---
            aggregated: dict[tuple[str, str], int] = {}

            for i in range(days):
                day_start = start_date + timedelta(days=i)
                day_end = day_start + timedelta(days=1) - timedelta(seconds=1)

                sec_data = await client.graphql(
                    SECURITY_QUERY % (
                        zone_id,
                        day_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        day_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    )
                )

                events = (
                    sec_data.get("data", {})
                    .get("viewer", {})
                    .get("zones", [{}])[0]
                    .get("firewallEventsAdaptiveGroups", [])
                )

                for event in events:
                    action = event["dimensions"]["action"]
                    country = event["dimensions"]["clientCountryName"]
                    key = (action, country)
                    aggregated[key] = aggregated.get(key, 0) + event["count"]

            # --- Aggregate calculations ---
            total_security_events = sum(aggregated.values())

            threat_percentage = (
                round((total_threats / total_requests) * 100, 2)
                if total_requests > 0
                else 0.0
            )

            cache_hit_rate = (
                round((total_cached / total_requests) * 100, 2)
                if total_requests > 0
                else 0.0
            )

            # Top countries (sum across all actions)
            country_totals: dict[str, int] = {}
            for (action, country), count in aggregated.items():
                country_totals[country] = country_totals.get(country, 0) + count
            top_countries = sorted(
                [{"country": c, "count": n} for c, n in country_totals.items()],
                key=lambda x: x["count"],
                reverse=True,
            )

            # Actions breakdown
            action_totals: dict[str, int] = {}
            for (action, country), count in aggregated.items():
                action_totals[action] = action_totals.get(action, 0) + count
            actions_breakdown = [
                {"action": a, "count": n}
                for a, n in sorted(action_totals.items(), key=lambda x: x[1], reverse=True)
            ]

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data={
                    "domain": target,
                    "days": days,
                    "total_requests": total_requests,
                    "total_cached": total_cached,
                    "total_threats": total_threats,
                    "threat_percentage": threat_percentage,
                    "cache_hit_rate": cache_hit_rate,
                    "total_security_events": total_security_events,
                    "top_countries": top_countries,
                    "actions_breakdown": actions_breakdown,
                },
                message=(
                    f"{target}: {total_requests:,} requests over {days}d, "
                    f"{threat_percentage}% threats, cache hit {cache_hit_rate}%"
                ),
            )

        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Bot analysis failed for {target}: {e}",
                errors=[str(e)],
            )
