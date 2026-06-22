from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.config import NKPConfig
from core.logger import get_logger

log = get_logger("cloudflare.zone_inventory")


class ZoneInventorySkill(BaseSkill):
    name = "cloudflare.zone_inventory"
    description = "List all Cloudflare zones, cross-reference with site inventory, find gaps"
    optional_inputs = ["account"]

    async def run(self, **kwargs) -> SkillResult:
        account = kwargs.get("account") or kwargs.get("target")

        try:
            client = get_cf_client()
            config = NKPConfig()

            # Get all CF zones
            zones = await client.get_all_zones()
            cf_domains = {z["name"]: z for z in zones}

            # Get inventory sites (optionally filtered by account)
            if account and account != "all":
                inv_sites = config.get_sites_by_account(account)
            else:
                inv_sites = config.get_active_sites()
            inv_domains = {s["domain"] for s in inv_sites}

            # Cross-reference
            in_inventory = [d for d in cf_domains if d in inv_domains]
            not_in_inventory = [d for d in cf_domains if d not in inv_domains]
            missing_from_cf = [d for d in inv_domains if d not in cf_domains]

            zone_list = []
            for z in zones:
                zone_list.append({
                    "domain": z["name"],
                    "zone_id": z["id"],
                    "status": z.get("status", "unknown"),
                    "in_inventory": z["name"] in inv_domains,
                })

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data={
                    "total_zones": len(zones),
                    "in_inventory": len(in_inventory),
                    "not_in_inventory": len(not_in_inventory),
                    "not_in_inventory_domains": not_in_inventory,
                    "missing_from_cf": len(missing_from_cf),
                    "missing_from_cf_domains": missing_from_cf,
                    "zones": zone_list,
                },
                message=f"{len(zones)} zones found, {len(not_in_inventory)} not in inventory, {len(missing_from_cf)} missing from CF",
            )
        except Exception as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Zone inventory failed: {e}",
                errors=[str(e)],
            )
