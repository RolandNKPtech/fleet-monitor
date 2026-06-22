from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.purge_cache")

MAX_URLS_PER_CALL = 30


class PurgeCacheSkill(BaseSkill):
    name = "cloudflare.purge_cache"
    description = "Purge Cloudflare cache for a domain — everything or specific URLs"
    required_inputs = ["target"]
    optional_inputs = ["urls"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]
        urls = kwargs.get("urls")

        if urls is not None and len(urls) > MAX_URLS_PER_CALL:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Too many URLs: {len(urls)} provided, max is {MAX_URLS_PER_CALL}",
                errors=[f"URL count {len(urls)} exceeds maximum of {MAX_URLS_PER_CALL}"],
            )

        try:
            client = get_cf_client()
            zone_id = await client.get_zone_id(target)

            if urls:
                body = {"files": urls}
                log.info(f"Purging {len(urls)} URLs for {target} (zone {zone_id})")
            else:
                body = {"purge_everything": True}
                log.info(f"Purging everything for {target} (zone {zone_id})")

            await client.post(f"/zones/{zone_id}/purge_cache", json=body)

            if urls:
                message = f"{target}: purged {len(urls)} URL(s)"
            else:
                message = f"{target}: purged everything"

            return SkillResult(
                status=SkillStatus.SUCCESS,
                data={
                    "domain": target,
                    "zone_id": zone_id,
                    "purge_everything": not bool(urls),
                    "urls": urls or [],
                },
                message=message,
            )

        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Cache purge failed for {target}: {e}",
                errors=[str(e)],
            )
