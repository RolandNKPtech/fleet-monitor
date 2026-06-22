from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.config import NKPConfig
from core.logger import get_logger

log = get_logger("cloudflare.wpe_crossref")


class WpeCrossrefSkill(BaseSkill):
    name = "cloudflare.wpe_crossref"
    description = "Cross-reference WPE sites (from sites.json) with Cloudflare zones, find gaps"
    optional_inputs = ["target"]

    async def run(self, **kwargs) -> SkillResult:
        target = kwargs.get("target")

        try:
            client = get_cf_client()
            config = NKPConfig()

            # Get CF zones
            zones = await client.get_all_zones()
            cf_domains = {z["name"] for z in zones}

            # Get WPE sites from sites.json (optionally filtered by account)
            if target and target != "all":
                wpe_sites = config.get_sites_by_account(target)
            else:
                wpe_sites = config.get_active_sites()
            wpe_domains = {s["domain"] for s in wpe_sites}

            # Cross-reference
            on_both = sorted(wpe_domains & cf_domains)
            wpe_only = sorted(wpe_domains - cf_domains)
            cf_only = sorted(cf_domains - wpe_domains)

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data={
                    "total_wpe": len(wpe_domains),
                    "total_cf": len(cf_domains),
                    "on_both": on_both,
                    "wpe_only": wpe_only,
                    "cf_only": cf_only,
                },
                message=(
                    f"{len(on_both)} on both, "
                    f"{len(wpe_only)} WPE-only (not in CF), "
                    f"{len(cf_only)} CF-only (not in sites.json)"
                ),
            )
        except Exception as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"WPE cross-reference failed: {e}",
                errors=[str(e)],
            )
